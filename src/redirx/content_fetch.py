"""
Content acquisition for embedding, cheapest-to-the-origin first.

Search Console tells us which URLs matter but returns no content, so pages
still have to be read from somewhere. Fetching them one at a time is the
expensive, fragile option — it is what draws WAF blocks, and it fails outright
once the old site is taken down.

Ladder:

  1. Platform API   WordPress REST / Shopify JSON. One paginated call returns
                    ~100 items with title and body already structured.
                    Measured on a real Elementor site: 3 calls for 147 pages.
                    Better input than scraping too — no nav, footer, or cookie
                    banner, so the embedding sees the article, not boilerplate.
  2. Live fetch     One request per page, paced by the shared host limiter.
  3. Wayback        Archived HTML, zero origin load. The only path that works
                    when the old site is already gone.

Live is tried before Wayback for accuracy — an archive snapshot can be years
stale — but Wayback is promoted ahead of it when the origin is unreachable or
has tripped the circuit breaker, which is its stated purpose.

Every tier is followed by a quality gate. A locked-down REST API returns 200
with empty bodies, and embedding those would produce confident-looking
nonsense; anything thin falls through to the next tier instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlparse

import aiohttp

from .rate_limit import CircuitOpen, get_limiter, parse_retry_after
from .stages import WebPage

logger = logging.getLogger(__name__)

WP_PER_PAGE = 100
WP_MAX_PAGES = 50
SHOPIFY_PER_PAGE = 250
SHOPIFY_MAX_PAGES = 20
WAYBACK_AVAILABILITY = "https://archive.org/wayback/available"
WAYBACK_CONCURRENCY = 4
HTTP_TIMEOUT = 30

# Below this many characters of extracted text a page is treated as unusable
# and falls through to the next tier. Real article bodies run into thousands;
# a stub or an empty API response lands far under this.
MIN_USABLE_TEXT = int(os.getenv("CONTENT_MIN_USABLE_TEXT", "200"))

SOURCE_PLATFORM_API = "platform_api"
SOURCE_LIVE = "live"
SOURCE_WAYBACK = "wayback"


def _key(url: str) -> str:
    """Scheme/www-insensitive identity, matching the rest of the codebase."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return f"{host}{parsed.path.rstrip('/')}"
    except Exception:
        return url.strip().rstrip("/").lower()


def is_usable(page: Optional[WebPage]) -> bool:
    """Whether a fetched page carries enough text to embed meaningfully."""
    if page is None or not page.html:
        return False
    try:
        return len(page.extract_text()) >= MIN_USABLE_TEXT
    except Exception:
        return False


def _page_from_parts(url: str, title: str, body_html: str) -> WebPage:
    """
    Build a WebPage from structured API fields.

    Downstream stages only know how to read HTML, so the title is re-emitted as
    a <title> element rather than threading a second content shape through the
    pipeline.
    """
    safe_title = (title or "").replace("<", "&lt;").replace(">", "&gt;")
    html = f"<html><head><title>{safe_title}</title></head><body>{body_html or ''}</body></html>"
    return WebPage(url, html)


class ContentFetcher:
    """Resolves URLs to WebPages using the cheapest tier that yields real text."""

    def __init__(self, session: aiohttp.ClientSession, enable_wayback: bool = True):
        self.session = session
        self.enable_wayback = enable_wayback
        self.stats: dict[str, int] = {
            SOURCE_PLATFORM_API: 0,
            SOURCE_LIVE: 0,
            SOURCE_WAYBACK: 0,
            "failed": 0,
        }

    # ---- tier 1: platform APIs ------------------------------------------

    async def _wp_bulk(self, root_url: str) -> dict[str, WebPage]:
        """Pull posts and pages with content from the WordPress REST API."""
        out: dict[str, WebPage] = {}
        for kind in ("posts", "pages"):
            page = 1
            while page <= WP_MAX_PAGES:
                url = (
                    f"{root_url}/wp-json/wp/v2/{kind}"
                    f"?per_page={WP_PER_PAGE}&page={page}&_fields=link,title,content"
                )
                try:
                    async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as resp:
                        if resp.status != 200:
                            break
                        total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or 1)
                        items = json.loads(await resp.read())
                except Exception as exc:
                    logger.info("WordPress API unavailable for %s: %s", root_url, type(exc).__name__)
                    break

                if not isinstance(items, list) or not items:
                    break
                for item in items:
                    link = (item or {}).get("link")
                    if not link:
                        continue
                    out[_key(link)] = _page_from_parts(
                        link,
                        ((item.get("title") or {}).get("rendered") or ""),
                        ((item.get("content") or {}).get("rendered") or ""),
                    )
                if page >= total_pages:
                    break
                page += 1
        return out

    async def _shopify_bulk(self, root_url: str) -> dict[str, WebPage]:
        """Pull products and their descriptions from Shopify's storefront JSON."""
        out: dict[str, WebPage] = {}
        page = 1
        while page <= SHOPIFY_MAX_PAGES:
            url = f"{root_url}/products.json?limit={SHOPIFY_PER_PAGE}&page={page}"
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as resp:
                    if resp.status != 200:
                        break
                    items = (json.loads(await resp.read()) or {}).get("products", [])
            except Exception as exc:
                logger.info("Shopify API unavailable for %s: %s", root_url, type(exc).__name__)
                break

            if not items:
                break
            for item in items:
                handle = (item or {}).get("handle")
                if not handle:
                    continue
                link = f"{root_url}/products/{handle}"
                out[_key(link)] = _page_from_parts(
                    link, item.get("title") or "", item.get("body_html") or ""
                )
            if len(items) < SHOPIFY_PER_PAGE:
                break
            page += 1
        return out

    async def fetch_platform(self, root_url: str, generator: Optional[str]) -> dict[str, WebPage]:
        if generator == "wordpress":
            return await self._wp_bulk(root_url)
        if generator == "shopify":
            return await self._shopify_bulk(root_url)
        return {}

    # ---- tier 2: live ----------------------------------------------------

    async def fetch_live(self, urls: Iterable[str], concurrency: int = 8) -> dict[str, WebPage]:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(url: str) -> tuple[str, Optional[WebPage]]:
            async with semaphore:
                try:
                    return url, await WebPage.scrape(self.session, url, max_retries=2)
                except CircuitOpen:
                    return url, None
                except Exception:
                    return url, None

        results = await asyncio.gather(*(one(u) for u in urls))
        return {_key(u): p for u, p in results if p is not None and p.html}

    # ---- tier 3: wayback -------------------------------------------------

    async def _wayback_one(self, url: str) -> Optional[WebPage]:
        try:
            async with self.session.get(
                WAYBACK_AVAILABILITY,
                params={"url": url},
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                payload = json.loads(await resp.read())
        except Exception:
            return None

        snapshot = ((payload.get("archived_snapshots") or {}).get("closest") or {})
        if not snapshot.get("available") or not snapshot.get("url"):
            return None

        # id_ returns the original bytes without the Wayback chrome injected.
        archived = snapshot["url"].replace("/http", "id_/http", 1)
        try:
            async with self.session.get(
                archived, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text(errors="replace")
        except Exception:
            return None
        return WebPage(url, html) if html else None

    async def fetch_wayback(self, urls: Iterable[str]) -> dict[str, WebPage]:
        if not self.enable_wayback:
            return {}
        semaphore = asyncio.Semaphore(WAYBACK_CONCURRENCY)

        async def one(url: str):
            async with semaphore:
                return url, await self._wayback_one(url)

        results = await asyncio.gather(*(one(u) for u in urls))
        return {_key(u): p for u, p in results if p is not None}

    # ---- orchestration ---------------------------------------------------

    async def fetch(
        self,
        urls: list[str],
        root_url: str,
        generator: Optional[str] = None,
        origin_reachable: bool = True,
    ) -> dict[str, WebPage]:
        """
        Resolve every URL to a WebPage, descending the ladder only for the ones
        still missing or too thin to embed.

        Args:
            origin_reachable: False promotes Wayback ahead of live fetching —
                the site is down or blocking us, so per-page requests would
                only produce failures and deepen a ban.
        """
        resolved: dict[str, WebPage] = {}
        wanted = {_key(u): u for u in urls}

        platform = await self.fetch_platform(root_url, generator)
        for k, page in platform.items():
            if k in wanted and is_usable(page):
                resolved[k] = page
        self.stats[SOURCE_PLATFORM_API] = len(resolved)
        if platform and not resolved:
            logger.info("platform API returned no usable content for %s", root_url)

        missing = [u for k, u in wanted.items() if k not in resolved]

        if missing and not origin_reachable:
            recovered = await self.fetch_wayback(missing)
            for k, page in recovered.items():
                if is_usable(page):
                    resolved[k] = page
            self.stats[SOURCE_WAYBACK] += len(recovered)
            missing = [u for k, u in wanted.items() if k not in resolved]

        if missing:
            live = await self.fetch_live(missing)
            added = 0
            for k, page in live.items():
                if is_usable(page):
                    resolved[k] = page
                    added += 1
            self.stats[SOURCE_LIVE] += added
            missing = [u for k, u in wanted.items() if k not in resolved]

        if missing and origin_reachable:
            # Last resort for pages the origin refused or served empty.
            recovered = await self.fetch_wayback(missing)
            added = 0
            for k, page in recovered.items():
                if is_usable(page):
                    resolved[k] = page
                    added += 1
            self.stats[SOURCE_WAYBACK] += added
            missing = [u for k, u in wanted.items() if k not in resolved]

        self.stats["failed"] = len(missing)
        return resolved

    def describe(self) -> str:
        return (
            f"platform_api={self.stats[SOURCE_PLATFORM_API]} "
            f"live={self.stats[SOURCE_LIVE]} "
            f"wayback={self.stats[SOURCE_WAYBACK]} "
            f"failed={self.stats['failed']}"
        )
