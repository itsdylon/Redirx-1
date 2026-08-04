"""
Domain URL discovery engine.

Given a root domain, discovers the site's page URLs through a chain of
strategies, cheapest and highest-quality first:

1. Sitemaps  — robots.txt directives + common paths, with sitemap-index
   recursion and gzip support. Covers the vast majority of real sites.
2. CMS APIs  — WordPress REST (/wp-json/wp/v2) and Shopify storefront JSON
   (/products.json, /collections.json) when the platform is detected and the
   sitemap was missing or thin.
3. Crawl     — same-host BFS from the homepage as the last resort.

All strategies respect an overall time budget so the caller (a synchronous
Flask request) can bound worst-case latency.
"""
from __future__ import annotations

import asyncio
import gzip
import ipaddress
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from redirx.stages import UrlPruneStage
from redirx.safe_fetch import (
    MAX_REDIRECTS,
    SSRFBlockedError,
    create_safe_connector,
    is_forbidden_ip,
    resolve_and_validate_host,
    validate_public_url,
)

USER_AGENT = "RedirxBot/1.0 (+https://redirx.dev; site migration redirect mapping)"

REQUEST_TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_SITEMAP_FETCHES = 100
SITEMAP_INDEX_MAX_DEPTH = 3
SITEMAP_FETCH_CONCURRENCY = 8
CRAWL_CONCURRENCY = 8
CRAWL_MAX_DEPTH = 4
WP_API_MAX_PAGES_PER_TYPE = 20
# Below this many sitemap URLs we assume the sitemap is missing/broken and
# fall through to the next strategy.
THIN_RESULT_THRESHOLD = 5

COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap.xml.gz",
    "/sitemap1.xml",
)


@dataclass
class DiscoveryResult:
    root_url: str
    urls: list[str] = field(default_factory=list)
    method: str = "none"  # sitemap | wordpress_api | shopify_api | crawl | none
    generator: str | None = None  # wordpress | shopify | webflow | ...
    total_found: int = 0
    truncated: bool = False
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)


class DiscoveryError(Exception):
    """User-facing discovery failure."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


# ============================================================================
# URL helpers (pure)
# ============================================================================

def normalize_root(raw: str) -> str:
    """
    Turn user input ("example.com", "http://www.example.com/path") into a
    canonical https root URL. Raises DiscoveryError on garbage input.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise DiscoveryError("invalid_domain", "Enter a domain to scan.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").strip(".").lower()
    if parsed.scheme not in ("http", "https") or not host or "." not in host:
        raise DiscoveryError(
            "invalid_domain",
            f"'{raw}' does not look like a valid domain.",
        )
    if parsed.port and parsed.port not in (80, 443):
        raise DiscoveryError("invalid_domain", "Cannot reach that host.")
    return f"{parsed.scheme}://{host}"


def is_private_host(hostname: str) -> bool:
    """Reject private/loopback targets to prevent SSRF (string-level check;
    resolved-IP validation happens at connect time via the safe connector)."""
    if hostname in ("localhost", "0.0.0.0") or hostname.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return is_forbidden_ip(hostname)


def _bare_host(url_or_host: str) -> str:
    host = urlparse(url_or_host).hostname if "://" in url_or_host else url_or_host
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(root_url: str, url: str) -> bool:
    """Same host, treating www. and bare domain as equivalent."""
    return _bare_host(root_url) == _bare_host(url)


def clean_page_url(url: str) -> str | None:
    """
    Normalize a discovered URL to a page URL: http(s) only, fragment dropped,
    query dropped (marketing params dominate; canonical paths matter for
    redirect mapping), assets pruned. Returns None if not a usable page.
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    cleaned = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path}"
    if not UrlPruneStage._sanitizer(cleaned):
        return None
    return cleaned


def dedupe_urls(urls: list[str]) -> list[str]:
    """Order-preserving dedupe, treating trailing-slash variants as equal."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append(url)
    return out


def parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """
    Parse one sitemap document.

    Returns:
        (child_sitemap_urls, page_urls) — one of the two will be empty for a
        well-formed document.
    """
    child_sitemaps: list[str] = []
    page_urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return child_sitemaps, page_urls

    local = root.tag.rsplit("}", 1)[-1]
    for element in root:
        el_local = element.tag.rsplit("}", 1)[-1]
        loc_text = None
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "loc":
                loc_text = (child.text or "").strip()
                break
        if not loc_text:
            continue
        if local == "sitemapindex" and el_local == "sitemap":
            child_sitemaps.append(loc_text)
        elif local == "urlset" and el_local == "url":
            page_urls.append(loc_text)
    return child_sitemaps, page_urls


def detect_generator(html: str, headers: dict) -> str | None:
    """Best-effort platform detection from homepage HTML + response headers."""
    header_keys = {k.lower() for k in headers}
    if "x-shopify-stage" in header_keys or "x-shopid" in header_keys:
        return "shopify"

    lowered = (html or "")[:200_000].lower()
    if not lowered:
        return None
    if "cdn.shopify.com" in lowered or "shopify.theme" in lowered:
        return "shopify"

    try:
        soup = BeautifulSoup(html[:200_000], "lxml")
        meta = soup.find("meta", attrs={"name": "generator"})
        content = (meta.get("content") or "").lower() if meta else ""
    except Exception:
        content = ""

    if "wordpress" in content or "/wp-content/" in lowered or "/wp-json" in lowered:
        return "wordpress"
    if "webflow" in content or 'data-wf-domain' in lowered:
        return "webflow"
    if "wix.com" in content or "wix.com" in lowered and "wix-code" in lowered:
        return "wix"
    if "squarespace" in content or "static1.squarespace.com" in lowered:
        return "squarespace"
    return content.split(" ")[0] if content else None


# ============================================================================
# Fetching
# ============================================================================

async def _fetch(
    session: aiohttp.ClientSession,
    url: str,
    errors: list[str],
) -> tuple[int, str, dict, str]:
    """
    GET a URL. Returns (status, text, headers, final_url); status 0 on
    transport failure or SSRF block. Transparently decompresses .gz sitemap
    payloads.

    Redirects are followed manually so every hop is re-validated (scheme,
    port, raw-IP denylist); the safe connector independently validates each
    hop's resolved IPs at connect time.
    """
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(current)
            async with session.get(current, allow_redirects=False) as resp:
                location = resp.headers.get("Location")
                if resp.status in (301, 302, 303, 307, 308) and location:
                    current = urljoin(current, location)
                    continue

                raw = await resp.content.read(MAX_RESPONSE_BYTES)
                headers = dict(resp.headers)
                final_url = str(resp.url)
                if current.endswith(".gz") and raw[:2] == b"\x1f\x8b":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        return resp.status, "", headers, final_url
                try:
                    text = raw.decode(resp.charset or "utf-8", errors="replace")
                except (LookupError, UnicodeDecodeError):
                    text = raw.decode("utf-8", errors="replace")
                return resp.status, text, headers, final_url

        errors.append(f"too many redirects for {url}")
        return 0, "", {}, current
    except SSRFBlockedError:
        errors.append(f"blocked target for {url}")
        return 0, "", {}, current
    except Exception as exc:
        errors.append(f"fetch failed for {url}: {type(exc).__name__}")
        return 0, "", {}, current


# ============================================================================
# Strategy 1: sitemaps
# ============================================================================

async def _sitemap_candidates(
    session: aiohttp.ClientSession,
    root_url: str,
    errors: list[str],
) -> list[str]:
    """Sitemap URLs from robots.txt plus the common conventional paths."""
    candidates: list[str] = []
    status, text, _, _ = await _fetch(session, f"{root_url}/robots.txt", errors)
    if status == 200:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("sitemap:"):
                candidates.append(stripped.split(":", 1)[1].strip())
    candidates.extend(f"{root_url}{path}" for path in COMMON_SITEMAP_PATHS)
    return dedupe_urls([c for c in candidates if c])


async def discover_via_sitemaps(
    session: aiohttp.ClientSession,
    root_url: str,
    collect_cap: int,
    deadline: float,
    errors: list[str],
) -> list[str]:
    """
    BFS over sitemap documents (indexes recurse) until the URL cap, fetch cap,
    or the deadline is hit.
    """
    queue = [(url, 0) for url in await _sitemap_candidates(session, root_url, errors)]
    visited: set[str] = set()
    page_urls: list[str] = []
    fetches = 0
    semaphore = asyncio.Semaphore(SITEMAP_FETCH_CONCURRENCY)

    async def fetch_one(sitemap_url: str) -> tuple[list[str], list[str]]:
        async with semaphore:
            status, text, _, _ = await _fetch(session, sitemap_url, errors)
        if status != 200 or not text:
            return [], []
        return parse_sitemap_xml(text)

    while queue and fetches < MAX_SITEMAP_FETCHES and len(page_urls) < collect_cap:
        if time.monotonic() > deadline:
            errors.append("sitemap discovery hit the time budget")
            break

        batch: list[tuple[str, int]] = []
        while queue and len(batch) < SITEMAP_FETCH_CONCURRENCY and fetches + len(batch) < MAX_SITEMAP_FETCHES:
            url, depth = queue.pop(0)
            key = url.rstrip("/")
            if key in visited or depth > SITEMAP_INDEX_MAX_DEPTH:
                continue
            visited.add(key)
            batch.append((url, depth))
        if not batch:
            break

        fetches += len(batch)
        results = await asyncio.gather(*(fetch_one(url) for url, _ in batch))
        for (_, depth), (children, pages) in zip(batch, results):
            queue.extend((child, depth + 1) for child in children)
            page_urls.extend(pages)

    return page_urls


# ============================================================================
# Strategy 2: CMS APIs
# ============================================================================

async def discover_via_wordpress(
    session: aiohttp.ClientSession,
    root_url: str,
    collect_cap: int,
    deadline: float,
    errors: list[str],
) -> list[str]:
    """Pull post + page permalinks from the WordPress REST API."""
    urls: list[str] = []
    for rest_type in ("pages", "posts"):
        for page in range(1, WP_API_MAX_PAGES_PER_TYPE + 1):
            if time.monotonic() > deadline or len(urls) >= collect_cap:
                return urls
            endpoint = (
                f"{root_url}/wp-json/wp/v2/{rest_type}"
                f"?per_page=100&page={page}&_fields=link"
            )
            status, text, _, _ = await _fetch(session, endpoint, errors)
            if status != 200:
                break
            try:
                import json
                items = json.loads(text)
            except ValueError:
                break
            if not isinstance(items, list) or not items:
                break
            urls.extend(
                item["link"] for item in items
                if isinstance(item, dict) and item.get("link")
            )
            if len(items) < 100:
                break
    return urls


async def discover_via_shopify(
    session: aiohttp.ClientSession,
    root_url: str,
    collect_cap: int,
    deadline: float,
    errors: list[str],
) -> list[str]:
    """Pull product + collection URLs from Shopify's storefront JSON."""
    import json

    urls: list[str] = []
    for resource, path_prefix in (("products", "/products/"), ("collections", "/collections/")):
        page = 1
        while len(urls) < collect_cap and page <= 20:
            if time.monotonic() > deadline:
                return urls
            endpoint = f"{root_url}/{resource}.json?limit=250&page={page}"
            status, text, _, _ = await _fetch(session, endpoint, errors)
            if status != 200:
                break
            try:
                items = json.loads(text).get(resource, [])
            except (ValueError, AttributeError):
                break
            if not items:
                break
            urls.extend(
                f"{root_url}{path_prefix}{item['handle']}"
                for item in items
                if isinstance(item, dict) and item.get("handle")
            )
            if len(items) < 250:
                break
            page += 1
    return urls


# ============================================================================
# Strategy 3: crawl
# ============================================================================

def extract_links(html: str, page_url: str, root_url: str) -> list[str]:
    """Same-site page links from an HTML document."""
    links: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return links
    for a_tag in soup.find_all("a", href=True):
        absolute = urljoin(page_url, a_tag["href"])
        if not same_site(root_url, absolute):
            continue
        cleaned = clean_page_url(absolute)
        if cleaned:
            links.append(cleaned)
    return links


async def discover_via_crawl(
    session: aiohttp.ClientSession,
    root_url: str,
    collect_cap: int,
    deadline: float,
    errors: list[str],
) -> list[str]:
    """Concurrent same-host BFS from the homepage."""
    start = clean_page_url(f"{root_url}/") or f"{root_url}/"
    visited: set[str] = set()
    found: list[str] = []
    frontier: list[tuple[str, int]] = [(start, 0)]
    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)
    max_fetches = min(collect_cap, 400)
    fetched = 0
    fetched_ok = 0

    async def fetch_page(url: str) -> str:
        async with semaphore:
            status, text, headers, _ = await _fetch(session, url, errors)
        if status != 200:
            return ""
        if "text/html" not in headers.get("Content-Type", headers.get("content-type", "")):
            return ""
        return text

    while frontier and fetched < max_fetches and len(found) < collect_cap:
        if time.monotonic() > deadline:
            errors.append("crawl hit the time budget")
            break

        batch: list[tuple[str, int]] = []
        while frontier and len(batch) < CRAWL_CONCURRENCY and fetched + len(batch) < max_fetches:
            url, depth = frontier.pop(0)
            key = url.rstrip("/")
            if key in visited or depth > CRAWL_MAX_DEPTH:
                continue
            visited.add(key)
            found.append(url)
            batch.append((url, depth))
        if not batch:
            break

        fetched += len(batch)
        pages = await asyncio.gather(*(fetch_page(url) for url, _ in batch))
        for (url, depth), html in zip(batch, pages):
            if not html:
                continue
            fetched_ok += 1
            if depth >= CRAWL_MAX_DEPTH:
                continue
            for link in extract_links(html, url, root_url):
                if link.rstrip("/") not in visited:
                    frontier.append((link, depth + 1))

    # URLs enter `found` when queued, so an unreachable host would otherwise
    # report its own homepage as a discovered page.
    if fetched_ok == 0:
        return []
    return found


# ============================================================================
# Orchestrator
# ============================================================================

def _filter_urls(raw_urls: list[str], root_url: str) -> list[str]:
    cleaned = []
    for url in raw_urls:
        page = clean_page_url(url)
        if page and same_site(root_url, page):
            cleaned.append(page)
    return dedupe_urls(cleaned)


async def discover_site(
    raw_root: str,
    max_urls: int = 1000,
    time_budget: float = 20.0,
) -> DiscoveryResult:
    """
    Discover a site's page URLs. Never raises for per-strategy failures —
    they are recorded in result.errors; raises DiscoveryError only for
    invalid/blocked input.
    """
    root_url = normalize_root(raw_root)
    host = urlparse(root_url).hostname or ""
    # Generic messages on purpose — do not confirm which targets are interesting.
    if is_private_host(host):
        raise DiscoveryError("ssrf_blocked", "Cannot reach that host.")
    # Resolve first, validate the resolved IPs, then connect. Catches public
    # hostnames that resolve to internal addresses (e.g. 10.0.0.1.nip.io).
    try:
        await resolve_and_validate_host(host, 443 if root_url.startswith("https") else 80)
    except SSRFBlockedError:
        raise DiscoveryError("ssrf_blocked", "Cannot reach that host.")

    started = time.monotonic()
    deadline = started + time_budget
    errors: list[str] = []
    result = DiscoveryResult(root_url=root_url)
    # Collect more than the cap so post-filtering still fills it.
    collect_cap = max_urls * 2

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        connector=create_safe_connector(),
    ) as session:
        # Homepage: platform detection + canonical root (follows redirects,
        # e.g. http→https, bare→www).
        status, html, headers, final_url = await _fetch(session, f"{root_url}/", errors)
        if status == 200 and final_url:
            final_root = normalize_root(final_url) if "://" in final_url else root_url
            if same_site(root_url, final_root):
                root_url = final_root
                result.root_url = root_url
        result.generator = detect_generator(html, headers) if status == 200 else None

        urls = _filter_urls(
            await discover_via_sitemaps(session, root_url, collect_cap, deadline, errors),
            root_url,
        )
        method = "sitemap" if urls else "none"

        if len(urls) < THIN_RESULT_THRESHOLD and result.generator == "wordpress":
            wp_urls = _filter_urls(
                await discover_via_wordpress(session, root_url, collect_cap, deadline, errors),
                root_url,
            )
            if len(wp_urls) > len(urls):
                urls, method = dedupe_urls(wp_urls + urls), "wordpress_api"

        if len(urls) < THIN_RESULT_THRESHOLD and result.generator == "shopify":
            shop_urls = _filter_urls(
                await discover_via_shopify(session, root_url, collect_cap, deadline, errors),
                root_url,
            )
            if len(shop_urls) > len(urls):
                urls, method = dedupe_urls(shop_urls + urls), "shopify_api"

        if len(urls) < THIN_RESULT_THRESHOLD and time.monotonic() < deadline:
            crawl_urls = _filter_urls(
                await discover_via_crawl(session, root_url, max_urls, deadline, errors),
                root_url,
            )
            if len(crawl_urls) > len(urls):
                urls, method = dedupe_urls(crawl_urls + urls), "crawl"

    result.total_found = len(urls)
    result.truncated = len(urls) > max_urls
    result.urls = urls[:max_urls]
    result.method = method if urls else "none"
    result.errors = errors
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
