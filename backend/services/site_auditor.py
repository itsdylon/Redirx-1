from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import urlparse, urljoin

import aiohttp
from bs4 import BeautifulSoup

from redirx.stages import UrlPruneStage, BlogPruneStage, WebPage
from redirx.safe_fetch import create_safe_connector


def _is_private_ip(hostname: str) -> bool:
    """Reject private/loopback IPs to prevent SSRF."""
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_reserved
    except ValueError:
        # Not a raw IP — it's a hostname, allow it
        return False


def _url_depth(url: str) -> int:
    """Count path segments (depth from root)."""
    path = urlparse(url).path.strip("/")
    if not path:
        return 0
    return len(path.split("/"))


def _categorize(url: str) -> str:
    """Categorize a URL by its path patterns."""
    if not UrlPruneStage._sanitizer(url):
        return "asset"
    if BlogPruneStage._is_blog_post(url):
        return "blog_post"

    path = urlparse(url).path.lower()

    # Blog landing pages
    if path.rstrip("/") in ("/blog", "/blogs", "/news"):
        return "blog_landing"

    # Product-like pages
    product_keywords = ("/product", "/shop", "/store", "/catalog", "/item", "/collection")
    if any(kw in path for kw in product_keywords):
        return "product_page"

    return "landing_page"


class SiteAuditor:
    """Crawl a site and yield SSE event dicts for real-time streaming."""

    DEFAULT_MAX_URLS = 50
    MIN_MAX_URLS = 1
    MAX_MAX_URLS = 200

    DEFAULT_SCRAPE_CONCURRENCY = 10
    MIN_SCRAPE_CONCURRENCY = 1
    MAX_SCRAPE_CONCURRENCY = 30

    def __init__(self, max_urls: int | None = None, scrape_concurrency: int | None = None):
        if max_urls is None:
            max_urls = self._bounded_env_int(
                "SITE_AUDITOR_MAX_URLS",
                self.DEFAULT_MAX_URLS,
                self.MIN_MAX_URLS,
                self.MAX_MAX_URLS,
            )
        if scrape_concurrency is None:
            scrape_concurrency = self._bounded_env_int(
                "SITE_AUDITOR_SCRAPE_MAX_CONCURRENT",
                self.DEFAULT_SCRAPE_CONCURRENCY,
                self.MIN_SCRAPE_CONCURRENCY,
                self.MAX_SCRAPE_CONCURRENCY,
            )

        self.max_urls = max_urls
        self.scrape_concurrency = scrape_concurrency

    @staticmethod
    def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(minimum, min(maximum, value))

    async def run_audit(self, url: str):
        """Async generator that yields SSE event dicts."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # SSRF check
        if _is_private_ip(parsed.hostname or ""):
            yield {"event": "error", "data": {"message": "Private/local addresses are not allowed", "code": "ssrf_blocked"}}
            return

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Redirx-SiteAudit/1.0"},
            connector=create_safe_connector(),
        ) as session:

            # --- URL Discovery ---
            discovered: list[str] = []
            discovery_method = "none"

            # 1. Try sitemap.xml
            yield {"event": "progress", "data": {"phase": "discovery", "message": "Trying sitemap.xml...", "urls_found": 0}}
            sitemap_urls = await self._try_sitemap(session, base_url)
            if sitemap_urls:
                discovered.extend(sitemap_urls)
                discovery_method = "sitemap"

            # 2. Try robots.txt for Sitemap directives
            if not discovered:
                yield {"event": "progress", "data": {"phase": "discovery", "message": "Trying robots.txt...", "urls_found": 0}}
                robots_urls = await self._try_robots_txt(session, base_url)
                if robots_urls:
                    discovered.extend(robots_urls)
                    discovery_method = "robots_txt"

            # 3. Recursive crawl as fallback
            if not discovered:
                yield {"event": "progress", "data": {"phase": "discovery", "message": "Crawling site links...", "urls_found": 0}}
                crawl_urls = await self._recursive_crawl(session, base_url)
                if crawl_urls:
                    discovered.extend(crawl_urls)
                    discovery_method = "crawl"

            # Deduplicate
            seen = set()
            unique_urls: list[str] = []
            for u in discovered:
                normalized = u.rstrip("/")
                if normalized not in seen:
                    seen.add(normalized)
                    unique_urls.append(u)

            # Filter through UrlPruneStage (but not the base sitemap URL itself)
            filtered_urls = [u for u in unique_urls if UrlPruneStage._sanitizer(u)]

            total_discovered = len(filtered_urls)

            if total_discovered == 0:
                yield {"event": "error", "data": {"message": f"Could not discover any pages on {parsed.netloc}", "code": "no_urls"}}
                return

            # Cap at configured max URLs
            urls_to_scrape = filtered_urls[:self.max_urls]

            yield {"event": "progress", "data": {
                "phase": "discovery",
                "message": f"Found {total_discovered} pages" + (f" (auditing first {self.max_urls})" if total_discovered > self.max_urls else ""),
                "urls_found": total_discovered
            }}

            # --- Page Analysis ---
            semaphore = asyncio.Semaphore(self.scrape_concurrency)
            pages: list[dict] = []
            content_hashes: dict[int, list[str]] = defaultdict(list)
            issues_summary: dict[str, list[str]] = defaultdict(list)
            categories: dict[str, int] = defaultdict(int)
            max_depth = 0

            for i, page_url in enumerate(urls_to_scrape):
                yield {"event": "progress", "data": {
                    "phase": "scraping",
                    "message": f"Scraping page {i + 1} of {len(urls_to_scrape)}...",
                    "pages_scraped": i,
                    "total_pages": len(urls_to_scrape)
                }}

                page_data = await self._analyze_page(session, semaphore, page_url)
                pages.append(page_data)

                # Track duplicates via content hash
                if page_data["content_hash"] is not None:
                    content_hashes[page_data["content_hash"]].append(page_url)

                # Track categories
                categories[page_data["category"]] += 1

                # Track depth
                if page_data["depth"] > max_depth:
                    max_depth = page_data["depth"]

                # Track issues
                for issue in page_data["issues"]:
                    issues_summary[issue].append(page_url)

                # Yield per-page event
                yield {"event": "page", "data": {
                    "url": page_data["url"],
                    "title": page_data["title"],
                    "content_length": page_data["content_length"],
                    "category": page_data["category"],
                    "depth": page_data["depth"],
                    "issues": page_data["issues"]
                }}

            # Mark duplicate content as an issue
            for h, urls in content_hashes.items():
                if len(urls) > 1:
                    for u in urls:
                        issues_summary["duplicate_content"].append(u)
                        # Also add to the individual page data
                        for p in pages:
                            if p["url"] == u and "duplicate_content" not in p["issues"]:
                                p["issues"].append("duplicate_content")

            # --- Complete Event ---
            yield {"event": "complete", "data": {
                "summary": {
                    "total_pages": total_discovered,
                    "pages_audited": len(pages),
                    "total_issues": sum(len(urls) for urls in issues_summary.values()),
                    "max_depth": max_depth,
                    "discovery_method": discovery_method,
                },
                "categories": dict(categories),
                "issues": {k: {"count": len(set(v)), "urls": list(set(v))} for k, v in issues_summary.items()},
                "pages": [{
                    "url": p["url"],
                    "title": p["title"],
                    "content_length": p["content_length"],
                    "category": p["category"],
                    "depth": p["depth"],
                    "issues": p["issues"]
                } for p in pages]
            }}

    async def _analyze_page(self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, url: str) -> dict:
        """Scrape and analyze a single page."""
        async with semaphore:
            page = await WebPage.scrape(session, url, max_retries=2)

        issues: list[str] = []
        title = ""
        content_length = 0
        content_hash = None

        if not page.html:
            issues.append("scrape_failed")
        else:
            title = page.extract_title()
            text = page.extract_text()
            content_length = len(text)
            content_hash = hash(page)

            if not title:
                issues.append("missing_title")
            if content_length < 200:
                issues.append("thin_content")

        category = _categorize(url)
        depth = _url_depth(url)

        return {
            "url": url,
            "title": title,
            "content_length": content_length,
            "category": category,
            "depth": depth,
            "issues": issues,
            "content_hash": content_hash,
        }

    # ---- URL Discovery Methods ----

    # How many child documents of a sitemap index to fetch. WordPress splits by
    # post type, so a handful covers the whole site.
    MAX_SITEMAP_CHILDREN = 10

    async def _try_sitemap(self, session: aiohttp.ClientSession, base_url: str) -> list[str]:
        """Fetch /sitemap.xml and extract page URLs, expanding a sitemap index."""
        return await self._collect_sitemap(session, f"{base_url}/sitemap.xml", base_url)

    async def _collect_sitemap(
        self, session: aiohttp.ClientSession, sitemap_url: str, base_url: str
    ) -> list[str]:
        """
        Page URLs from one sitemap document.

        A sitemap index lists other sitemaps, not pages, so its children have to
        be fetched. Returning the child document URLs instead — as this did —
        yielded a non-empty list that suppressed the robots.txt and crawl
        fallbacks, and then got stripped by UrlPruneStage for being .xml. Every
        site whose /sitemap.xml is an index (Yoast, Rank Math, most WordPress)
        audited as "Could not discover any pages".
        """
        try:
            text = await self._fetch_text(session, sitemap_url)
            if text is None:
                return []
            locs, is_index = self._parse_sitemap_xml(text)
            if not is_index:
                return locs

            urls: list[str] = []
            for child in locs[: self.MAX_SITEMAP_CHILDREN]:
                child_text = await self._fetch_text(session, child)
                if child_text is None:
                    continue
                child_locs, child_is_index = self._parse_sitemap_xml(child_text)
                # One level of expansion only; nested indexes are vanishingly rare.
                if not child_is_index:
                    urls.extend(child_locs)
            return urls
        except Exception:
            return []

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str | None:
        """Body of a sitemap document, or None if it is not usable."""
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except Exception:
            return None

    def _parse_sitemap_xml(self, xml_text: str) -> tuple[list[str], bool]:
        """
        Parse a sitemap document.

        Returns (locs, is_index). When is_index is True the locs are other
        sitemap documents; otherwise they are pages.
        """
        locs: list[str] = []
        try:
            root = ET.fromstring(xml_text)
            ns = ""
            # Handle namespace
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            is_index = root.tag == f"{ns}sitemapindex"
            container = f"{ns}sitemap" if is_index else f"{ns}url"
            for elem in root.findall(container):
                loc = elem.find(f"{ns}loc")
                if loc is not None and loc.text:
                    locs.append(loc.text.strip())
            return locs, is_index
        except ET.ParseError:
            return [], False

    async def _try_robots_txt(self, session: aiohttp.ClientSession, base_url: str) -> list[str]:
        """Fetch /robots.txt, extract Sitemap: directives, fetch those sitemaps."""
        urls: list[str] = []
        try:
            async with session.get(f"{base_url}/robots.txt") as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()

            sitemap_urls = []
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    sitemap_urls.append(sitemap_url)

            # Fetch up to 5 sitemaps. These are usually indexes too, so they go
            # through the same expansion as /sitemap.xml.
            for sm_url in sitemap_urls[:5]:
                urls.extend(await self._collect_sitemap(session, sm_url, base_url))
        except Exception:
            pass
        return urls

    async def _recursive_crawl(self, session: aiohttp.ClientSession, base_url: str, max_depth: int = 2) -> list[str]:
        """BFS crawl from homepage, following internal <a href> links."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc
        visited: set[str] = set()
        to_visit: list[tuple[str, int]] = [(base_url, 0)]
        found: list[str] = []

        while to_visit and len(found) < 200:  # Hard cap to avoid runaway crawls
            url, depth = to_visit.pop(0)
            normalized = url.rstrip("/")
            if normalized in visited:
                continue
            if depth > max_depth:
                continue

            visited.add(normalized)
            found.append(url)

            if depth >= max_depth:
                continue

            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        continue
                    content_type = resp.headers.get("Content-Type", "")
                    if "text/html" not in content_type:
                        continue
                    html = await resp.text()
            except Exception:
                continue

            try:
                soup = BeautifulSoup(html, "lxml")
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    absolute = urljoin(url, href)
                    parsed_link = urlparse(absolute)

                    # Same-domain internal links only
                    if parsed_link.netloc != base_domain:
                        continue

                    # Strip fragment
                    clean_url = f"{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}"
                    if parsed_link.query:
                        clean_url += f"?{parsed_link.query}"

                    if clean_url.rstrip("/") not in visited:
                        to_visit.append((clean_url, depth + 1))
            except Exception:
                continue

        return found
