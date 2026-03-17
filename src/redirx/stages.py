from __future__ import annotations

import aiohttp
import asyncio
import os
from bs4 import BeautifulSoup
from typing import Optional, Callable
from uuid import UUID, uuid4
import numpy as np
from openai import AsyncOpenAI

from .config import Config
from .database import WebPageEmbeddingDB, MigrationSessionDB, URLMappingDB
from .url_matcher import UrlMatcher, UrlMatchConfig

class Stage:
    name: str = "Processing"

    def __init__(self):
        pass

    async def execute(self, input: any) -> any:
        raise NotImplementedError("The stage must overwrite execute.")


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer env var and clamp it into [minimum, maximum]."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        print(f"⚠️  {name}={raw!r} is invalid; using default {default}", flush=True)
        value = default

    if value < minimum:
        print(f"⚠️  {name}={value} below minimum {minimum}; clamping", flush=True)
        return minimum
    if value > maximum:
        print(f"⚠️  {name}={value} above maximum {maximum}; clamping", flush=True)
        return maximum
    return value


# =========================
# URL Prune Stage
# =========================

class UrlPruneStage(Stage):
    """
    Filters out non-HTML URLs (assets like CSS, JS, images, etc.).
    Only allows HTML pages and URLs without file extensions.
    """
    name = "Filtering URLs"

    # File extensions that should be filtered out
    BLOCKED_EXTENSIONS = {
        '.css', '.js', '.json', '.xml',  # Web assets
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp',  # Images
        '.woff', '.woff2', '.ttf', '.eot', '.otf',  # Fonts
        '.pdf', '.zip', '.tar', '.gz', '.rar',  # Documents/Archives
        '.mp4', '.mp3', '.avi', '.mov', '.wav',  # Media
        '.txt', '.csv', '.log',  # Data files
    }

    def __init__(self):
        super().__init__()

    @staticmethod
    def _sanitizer(url: str) -> bool:
        """
        Determine if a URL should be included in processing.

        Args:
            url: The URL to check

        Returns:
            True if URL should be processed (is HTML or no extension)
            False if URL should be filtered out (is an asset file)
        """
        # Extract path from URL (handle both full URLs and paths)
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            path = parsed.path

            # Get the file extension
            if '.' in path:
                # Get the last part after the last dot
                extension = '.' + path.rsplit('.', 1)[-1].lower()

                # Filter out if extension is in blocked list
                if extension in UrlPruneStage.BLOCKED_EXTENSIONS:
                    return False

            # Allow HTML files explicitly
            if path.endswith('.html') or path.endswith('.htm'):
                return True

            # Allow URLs without file extensions (likely pages)
            if '.' not in path.split('/')[-1]:
                return True

            # Allow other cases (e.g., .html, .php, etc.)
            return True

        except Exception:
            # If parsing fails, allow it (be permissive on errors)
            return True

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        """
        Filter URLs to remove asset files.

        Args:
            input: Tuple of (old_urls, new_urls)

        Returns:
            Tuple of (filtered_old_urls, filtered_new_urls)
        """
        raw_old_urls, raw_new_urls = input

        sanitized_old_urls = [url for url in raw_old_urls if UrlPruneStage._sanitizer(url)]
        sanitized_new_urls = [url for url in raw_new_urls if UrlPruneStage._sanitizer(url)]

        return (sanitized_old_urls, sanitized_new_urls)


# =========================
# Blog Prune Stage
# =========================

class BlogPruneStage(Stage):
    """
    Filters out individual blog posts from old site.
    Keeps blog landing pages like /blog, /blogs/index.html, /news/index.html.

    This stage runs BEFORE scraping to avoid wasting HTTP requests on pages
    we don't want to redirect (individual blog posts should not be redirected for SEO).
    """
    name = "Filtering blog posts"

    def __init__(self):
        super().__init__()

    @staticmethod
    def _is_blog_post(url: str) -> bool:
        """
        Detect if a URL is an individual blog post (not a landing page).

        Patterns for blog posts:
        - /blog/YYYY-*.html (dated posts)
        - /blogs/YYYY-*.html (dated posts)
        - /news/YYYY-*.html (dated news articles)
        - /blogs/*.html EXCEPT /blogs/index.html
        - /blog/*.html EXCEPT /blog/index.html

        Keep landing pages:
        - /blog (no file)
        - /blogs/index.html
        - /news/index.html
        """
        from urllib.parse import urlparse
        import re

        try:
            parsed = urlparse(url)
            path = parsed.path.lower()

            # Keep landing pages explicitly
            if path.endswith('/blog') or path.endswith('/blogs') or path.endswith('/news'):
                return False
            if path.endswith('/index.html') and ('/blog' in path or '/news' in path):
                return False

            # Detect dated blog posts (YYYY-MM-title or YYYY-title)
            if re.search(r'/(blogs?|news)/\d{4}-', path):
                return True

            # Detect individual posts in blog/news directories (but not index)
            if '/blog/' in path and not path.endswith('/index.html'):
                if path.endswith('.html') or path.endswith('.htm'):
                    return True
            if '/news/' in path and not path.endswith('/index.html'):
                if path.endswith('.html') or path.endswith('.htm'):
                    return True

            return False

        except Exception:
            # If parsing fails, keep it (be permissive on errors)
            return False

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        """
        Filter blog posts from old URLs.

        Args:
            input: Tuple of (old_urls, new_urls)

        Returns:
            Tuple of (filtered_old_urls, new_urls) - new_urls unchanged
        """
        old_urls, new_urls = input

        # Filter blog posts from old site only
        filtered_old_urls = [url for url in old_urls if not self._is_blog_post(url)]
        removed_count = len(old_urls) - len(filtered_old_urls)

        if removed_count > 0:
            print(f"\nBlogPruneStage: Filtered {removed_count} individual blog posts from old site")
            print(f"BlogPruneStage: Kept {len(filtered_old_urls)} old URLs")

        return (filtered_old_urls, new_urls)


# =========================
# Exact URL Match Stage
# =========================

class ExactUrlMatchStage(Stage):
    """
    Matches URLs with identical paths (ignoring domain).
    Runs BEFORE scraping to avoid wasting HTTP requests on obvious matches.

    Example: http://old.com/products/index.html → http://new.com/products/index.html
    """
    name = "Matching exact URLs"

    def __init__(self, session_id: Optional[UUID] = None):
        super().__init__()
        self.session_id = session_id
        self.mapping_db = URLMappingDB() if session_id else None

    # Extensions to strip during path normalization
    _STRIP_EXTENSIONS = {'.html', '.htm', '.php', '.asp', '.aspx', '.jsp', '.shtml'}
    _INDEX_FILES = {'index', 'default'}

    @staticmethod
    def _get_path(url: str) -> str:
        """Extract and normalize path from URL (ignoring domain)."""
        from urllib.parse import urlparse, unquote
        try:
            # Add scheme if missing so urlparse separates domain from path
            if not url.startswith(('http://', 'https://', '//')):
                url = 'http://' + url

            parsed = urlparse(url)
            path = unquote(parsed.path).lower().rstrip('/')

            # Strip common file extensions (.html, .htm, .php, etc.)
            for ext in ExactUrlMatchStage._STRIP_EXTENSIONS:
                if path.endswith(ext):
                    path = path[:-len(ext)]
                    break

            # Remove trailing /index or /default (after extension strip)
            parts = path.split('/')
            if parts and parts[-1] in ExactUrlMatchStage._INDEX_FILES:
                parts = parts[:-1]

            path = '/'.join(parts) or '/'

            # Ensure path starts with /
            if not path.startswith('/'):
                path = '/' + path

            return path
        except Exception:
            return url

    @staticmethod
    def _common_path_prefix(paths: list[str]) -> str:
        """
        Find the common first-segment prefix among all paths.

        Fails fast — returns '' as soon as any path breaks the candidate prefix.
        Only O(n) when all paths genuinely share the same prefix.

        E.g., ['/mock-old/about', '/mock-old/contact'] → '/mock-old'
             ['/about', '/contact', '/products'] → '' (fails on URL #2)
        """
        candidate = None
        for path in paths:
            if path == '/':
                continue
            first_segment = path.strip('/').split('/')[0]
            if not first_segment:
                continue
            if candidate is None:
                candidate = first_segment
            elif first_segment != candidate:
                return ''  # Fail fast
        return '/' + candidate if candidate else ''

    @staticmethod
    def _strip_prefix(path: str, prefix: str) -> str:
        """Strip a base path prefix from a path, returning the relative portion."""
        if prefix and path.startswith(prefix):
            stripped = path[len(prefix):]
            return stripped if stripped.startswith('/') else '/' + stripped
        return path

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        """
        Find and remove exact URL path matches.

        Detects differing base path prefixes (e.g., /mock-old-site/ vs /mock-new-site/)
        and strips them before comparing, so same-domain sites with different path
        prefixes still match.

        Args:
            input: Tuple of (old_urls, new_urls)

        Returns:
            Tuple of (unmatched_old_urls, unmatched_new_urls)
        """
        old_urls, new_urls = input

        print(f"\nExactUrlMatchStage: Checking {len(old_urls)} old vs {len(new_urls)} new URLs for exact path matches...")

        # Deduplicate old URLs by normalized path (prefer shorter/cleaner URLs)
        old_url_by_path = {}
        for url in old_urls:
            path = self._get_path(url)
            if path not in old_url_by_path:
                old_url_by_path[path] = url
            else:
                if len(url) < len(old_url_by_path[path]):
                    old_url_by_path[path] = url

        # Build map of path -> new URL (also deduplicated)
        new_url_by_path = {}
        for url in new_urls:
            path = self._get_path(url)
            if path not in new_url_by_path:
                new_url_by_path[path] = url
            else:
                if len(url) < len(new_url_by_path[path]):
                    new_url_by_path[path] = url

        # Detect differing base path prefixes (e.g., /mock-old-site vs /mock-new-site)
        old_paths = list(old_url_by_path.keys())
        new_paths = list(new_url_by_path.keys())
        old_prefix = self._common_path_prefix(old_paths)
        new_prefix = self._common_path_prefix(new_paths)

        strip_prefixes = old_prefix != new_prefix and (old_prefix or new_prefix)
        if strip_prefixes:
            print(f"ExactUrlMatchStage: Detected different base paths: '{old_prefix}' (old) vs '{new_prefix}' (new) — stripping for comparison")
            # Rebuild maps keyed by stripped paths, values are still original URLs
            stripped_old = {}
            for path, url in old_url_by_path.items():
                stripped = self._strip_prefix(path, old_prefix)
                if stripped not in stripped_old or len(url) < len(stripped_old[stripped]):
                    stripped_old[stripped] = url
            stripped_new = {}
            for path, url in new_url_by_path.items():
                stripped = self._strip_prefix(path, new_prefix)
                if stripped not in stripped_new or len(url) < len(stripped_new[stripped]):
                    stripped_new[stripped] = url

            old_url_by_path = stripped_old
            new_url_by_path = stripped_new

        matched_pairs = []
        unmatched_old = []
        matched_new_paths = set()
        root_paths_skipped = 0

        for old_path, old_url in old_url_by_path.items():
            # Skip root paths - homepage always exists on both sites, no redirect needed
            if old_path == '/':
                root_paths_skipped += 1
                unmatched_old.append(old_url)
                continue

            if old_path in new_url_by_path:
                new_url = new_url_by_path[old_path]
                matched_pairs.append((old_url, new_url))
                matched_new_paths.add(old_path)
                print(f"✓ ExactUrlMatchStage: {old_path}")
            else:
                unmatched_old.append(old_url)

        # Remove matched new URLs — need to check stripped paths
        matched_new_urls = {url for _, url in matched_pairs}
        unmatched_new = [url for url in new_urls if url not in matched_new_urls]

        duplicates_removed = len(old_urls) - len(old_url_by_path)
        if duplicates_removed > 0:
            print(f"ExactUrlMatchStage: Deduplicated {duplicates_removed} old URL variants (e.g., / and /index.html)")

        if root_paths_skipped > 0:
            print(f"ℹ️  ExactUrlMatchStage: Skipped {root_paths_skipped} root path(s) - homepage doesn't need redirect")

        print(f"ExactUrlMatchStage: Found {len(matched_pairs)} exact URL matches")
        print(f"ExactUrlMatchStage: {len(unmatched_old)} old + {len(unmatched_new)} new URLs remain for scraping")

        # Store exact matches to database if session_id is set
        if self.session_id and self.mapping_db and matched_pairs:
            print(f"ExactUrlMatchStage: Inserting {len(matched_pairs)} exact matches to database...")
            for old_url, new_url in matched_pairs:
                self.mapping_db.insert_mapping(
                    session_id=self.session_id,
                    old_url=old_url,
                    new_url=new_url,
                    confidence_score=1.0,
                    match_type='exact_url',
                    needs_review=False
                )

        return (unmatched_old, unmatched_new)


# =========================
# URL Similarity Match Stage
# =========================

class UrlSimilarityMatchStage(Stage):
    """
    Matches URLs using slug matching, TF-IDF cosine similarity, and RapidFuzz fallback.
    Zero API cost — runs entirely locally. Used in the url_only pipeline (free tier).

    Input: tuple[list[str], list[str]] — unmatched URLs from ExactUrlMatchStage
    Output: tuple[list[str], list[str]] — remaining unmatched URLs
    """
    name = "Matching URLs by similarity"

    def __init__(self, session_id: Optional[UUID] = None, config: Optional[UrlMatchConfig] = None):
        super().__init__()
        self.session_id = session_id
        self.config = config or UrlMatchConfig()
        self.matcher = UrlMatcher(config=self.config)
        self.mapping_db = URLMappingDB() if session_id else None

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        old_urls, new_urls = input

        print(f"\nUrlSimilarityMatchStage: Matching {len(old_urls)} old vs {len(new_urls)} new URLs...")

        results = self.matcher.match(old_urls, new_urls)

        # Store matches in database
        if self.session_id and self.mapping_db and results:
            print(f"UrlSimilarityMatchStage: Inserting {len(results)} matches to database...")
            for r in results:
                self.mapping_db.insert_mapping(
                    session_id=self.session_id,
                    old_url=r.old_url,
                    new_url=r.new_url,
                    confidence_score=r.confidence_score,
                    match_type=r.match_type,
                    needs_review=r.needs_review,
                )

        # Compute remaining unmatched URLs
        matched_old = {r.old_url for r in results}
        matched_new = {r.new_url for r in results}
        unmatched_old = [u for u in old_urls if u not in matched_old]
        unmatched_new = [u for u in new_urls if u not in matched_new]

        print(f"UrlSimilarityMatchStage: {len(results)} matched, "
              f"{len(unmatched_old)} orphaned old, {len(unmatched_new)} unmatched new")

        return (unmatched_old, unmatched_new)


# =========================
# Web Scraper Stage
# =========================

class WebScraperStage(Stage):
    """
    Scrapes all URLs for their HTML content with comprehensive logging.
    """
    name = "Scraping webpages"
    DEFAULT_TOTAL_CONCURRENCY = 12
    DEFAULT_PER_SITE_CONCURRENCY = 8
    MIN_CONCURRENCY = 1
    MAX_TOTAL_CONCURRENCY = 64
    MAX_PER_SITE_CONCURRENCY = 32

    def __init__(
        self,
        max_total_concurrency: Optional[int] = None,
        max_site_concurrency: Optional[int] = None,
        session_id: Optional[UUID] = None,
        progress_stage: Optional[int] = None,
        total_stages: Optional[int] = None,
        progress_update_every: int = 10,
    ):
        super().__init__()
        if max_total_concurrency is None:
            total = _bounded_env_int(
                "SCRAPER_MAX_CONCURRENT_TOTAL",
                self.DEFAULT_TOTAL_CONCURRENCY,
                self.MIN_CONCURRENCY,
                self.MAX_TOTAL_CONCURRENCY,
            )
        else:
            total = max(
                self.MIN_CONCURRENCY,
                min(self.MAX_TOTAL_CONCURRENCY, int(max_total_concurrency)),
            )

        if max_site_concurrency is None:
            per_site = _bounded_env_int(
                "SCRAPER_MAX_CONCURRENT_PER_SITE",
                self.DEFAULT_PER_SITE_CONCURRENCY,
                self.MIN_CONCURRENCY,
                self.MAX_PER_SITE_CONCURRENCY,
            )
        else:
            per_site = max(
                self.MIN_CONCURRENCY,
                min(self.MAX_PER_SITE_CONCURRENCY, int(max_site_concurrency)),
            )

        self.max_total_concurrency = total
        self.max_site_concurrency = min(per_site, total)
        self.session_id = session_id
        self.progress_stage = progress_stage
        self.total_stages = total_stages
        self.progress_update_every = max(1, progress_update_every)
        self.session_db = (
            MigrationSessionDB()
            if self.session_id is not None and self.progress_stage and self.total_stages
            else None
        )
        self._last_reported_scraped = -1

    def _publish_scrape_progress(
        self,
        *,
        scraped_total: int,
        scrape_goal: int,
        old_scraped: int,
        new_scraped: int,
        force: bool = False,
    ) -> None:
        if (
            self.session_db is None
            or self.session_id is None
            or self.progress_stage is None
            or self.total_stages is None
        ):
            return

        if self._last_reported_scraped == scraped_total:
            return

        should_publish = force or scraped_total == scrape_goal
        if not should_publish:
            should_publish = (scraped_total % self.progress_update_every) == 0

        if not should_publish:
            return

        self._last_reported_scraped = scraped_total
        stage_name = (
            f"Scraping webpages ({scraped_total}/{scrape_goal} pages scraped, "
            f"old {old_scraped}, new {new_scraped})"
        )

        try:
            self.session_db.update_session_progress(
                self.session_id,
                self.progress_stage,
                stage_name,
                self.total_stages,
            )
        except Exception as exc:
            print(f"⚠️  WebScraperStage: failed to publish progress update ({exc})", flush=True)

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[WebPage], list[WebPage]]:
        old_urls, new_urls = input
        total_urls = len(old_urls) + len(new_urls)
        self._last_reported_scraped = -1

        print(
            f"\nWebScraperStage: Scraping {len(old_urls)} old + {len(new_urls)} new URLs "
            f"(total concurrency={self.max_total_concurrency}, "
            f"per-site concurrency={self.max_site_concurrency})...",
            flush=True,
        )
        self._publish_scrape_progress(
            scraped_total=0,
            scrape_goal=total_urls,
            old_scraped=0,
            new_scraped=0,
            force=True,
        )

        async with aiohttp.ClientSession() as session:
            total_semaphore = asyncio.Semaphore(self.max_total_concurrency)
            old_site_semaphore = asyncio.Semaphore(self.max_site_concurrency)
            new_site_semaphore = asyncio.Semaphore(self.max_site_concurrency)

            async def scrape_with_limits(url: str, site_semaphore: asyncio.Semaphore):
                async with total_semaphore:
                    async with site_semaphore:
                        return await WebPage.scrape(session, url)

            async def scrape_with_meta(
                url: str,
                site: str,
                index: int,
                site_semaphore: asyncio.Semaphore,
            ):
                page = await scrape_with_limits(url, site_semaphore)
                return site, index, page

            old_webpages: list[Optional[WebPage]] = [None] * len(old_urls)
            new_webpages: list[Optional[WebPage]] = [None] * len(new_urls)
            tasks: list[asyncio.Task] = []

            for idx, url in enumerate(old_urls):
                tasks.append(
                    asyncio.create_task(
                        scrape_with_meta(url, "old", idx, old_site_semaphore)
                    )
                )
            for idx, url in enumerate(new_urls):
                tasks.append(
                    asyncio.create_task(
                        scrape_with_meta(url, "new", idx, new_site_semaphore)
                    )
                )

            scraped_total = 0
            old_scraped = 0
            new_scraped = 0

            for finished in asyncio.as_completed(tasks):
                site, index, page = await finished
                if site == "old":
                    old_webpages[index] = page
                    old_scraped += 1
                else:
                    new_webpages[index] = page
                    new_scraped += 1
                scraped_total += 1
                self._publish_scrape_progress(
                    scraped_total=scraped_total,
                    scrape_goal=total_urls,
                    old_scraped=old_scraped,
                    new_scraped=new_scraped,
                )

        old_webpages_final = [p for p in old_webpages if p is not None]
        new_webpages_final = [p for p in new_webpages if p is not None]
        self._publish_scrape_progress(
            scraped_total=total_urls,
            scrape_goal=total_urls,
            old_scraped=len(old_webpages_final),
            new_scraped=len(new_webpages_final),
            force=True,
        )

        # Log scraping results
        old_success = sum(1 for p in old_webpages_final if len(p.html) > 0)
        old_failed = len(old_webpages_final) - old_success
        new_success = sum(1 for p in new_webpages_final if len(p.html) > 0)
        new_failed = len(new_webpages_final) - new_success

        print(f"WebScraperStage: Old site - {old_success} succeeded, {old_failed} failed")
        print(f"WebScraperStage: New site - {new_success} succeeded, {new_failed} failed")

        # Log pages with empty HTML (scraping failures)
        if old_failed > 0:
            print(f"⚠️  WebScraperStage: Failed to scrape {old_failed} old pages:")
            for page in old_webpages_final:
                if len(page.html) == 0:
                    print(f"   - {page.url}")

        if new_failed > 0:
            print(f"⚠️  WebScraperStage: Failed to scrape {new_failed} new pages:")
            for page in new_webpages_final:
                if len(page.html) == 0:
                    print(f"   - {page.url}")

        # Log HTML sizes for successful scrapes
        if old_success > 0:
            avg_old_size = sum(len(p.html) for p in old_webpages_final if len(p.html) > 0) / old_success
            print(f"WebScraperStage: Old pages avg HTML size: {int(avg_old_size)} bytes")

        if new_success > 0:
            avg_new_size = sum(len(p.html) for p in new_webpages_final if len(p.html) > 0) / new_success
            print(f"WebScraperStage: New pages avg HTML size: {int(avg_new_size)} bytes")

        return (old_webpages_final, new_webpages_final)

# =========================
# HTML Prune Stage
# =========================

class HtmlPruneStage(Stage):
    """
    Matches pages with identical HTML content.
    Skips pages with empty or very short HTML to avoid false matches from scraping failures.
    """
    name = "Matching HTML content"

    # Minimum HTML length to consider for matching (bytes)
    MIN_HTML_LENGTH = 100

    def __init__(self):
        super().__init__()

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage]]
    ) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:

        old_pages, new_pages = input

        # Filter out pages with empty/short HTML
        valid_new_pages = [p for p in new_pages if len(p.html) >= self.MIN_HTML_LENGTH]
        valid_old_pages = [p for p in old_pages if len(p.html) >= self.MIN_HTML_LENGTH]

        # Log filtering
        skipped_old = len(old_pages) - len(valid_old_pages)
        skipped_new = len(new_pages) - len(valid_new_pages)
        if skipped_old > 0 or skipped_new > 0:
            print(f"⚠️  HtmlPruneStage: Skipped {skipped_old} old + {skipped_new} new pages with HTML < {self.MIN_HTML_LENGTH} bytes")

        # Build hash map of valid new pages
        new_page_map = {hash(page): page for page in valid_new_pages}

        # Check for hash collisions (suspicious if many pages have same hash)
        if len(new_page_map) < len(valid_new_pages):
            collision_count = len(valid_new_pages) - len(new_page_map)
            print(f"⚠️  HtmlPruneStage: {collision_count} hash collisions detected - some new pages have identical HTML")

        mappings = set()
        matched_new_hashes = set()

        for page in valid_old_pages:
            page_hash = hash(page)
            if page_hash in new_page_map and page_hash not in matched_new_hashes:
                # Exact HTML match - highest confidence, no review needed
                new_page = new_page_map[page_hash]
                mappings.add(Mapping(
                    old_page=page,
                    new_page=new_page,
                    confidence_score=1.0,
                    match_type='exact_html',
                    needs_review=False
                ))
                matched_new_hashes.add(page_hash)
                print(f"✓ HtmlPruneStage: Exact HTML match - {page.url} → {new_page.url}")

        print(f"HtmlPruneStage: Found {len(mappings)} exact HTML matches")
        return (old_pages, new_pages, mappings)


# =========================
# Embedding Stage
# =========================

class EmbedStage(Stage):
    name = "Generating embeddings"

    def __init__(
        self,
        session_id: Optional[UUID] = None,
        progress_stage: Optional[int] = None,
        total_stages: Optional[int] = None,
    ):
        super().__init__()
        self.session_id = session_id
        self.embedding_db = WebPageEmbeddingDB()
        self.session_db = MigrationSessionDB()
        self.openai_client: Optional[AsyncOpenAI] = None
        self.progress_stage = progress_stage
        self.total_stages = total_stages
        self._last_reported_embeddings = -1

    def _publish_embedding_progress(
        self,
        *,
        generated_count: int,
        target_count: int,
        failed_count: int = 0,
        force: bool = False,
    ) -> None:
        if (
            self.session_id is None
            or self.progress_stage is None
            or self.total_stages is None
        ):
            return

        if self._last_reported_embeddings == generated_count and not force:
            return

        self._last_reported_embeddings = generated_count
        stage_name = f"Generating embeddings ({generated_count}/{target_count} generated)"
        if failed_count > 0:
            stage_name = (
                f"Generating embeddings ({generated_count}/{target_count} generated, "
                f"{failed_count} failed)"
            )

        try:
            self.session_db.update_session_progress(
                self.session_id,
                self.progress_stage,
                stage_name,
                self.total_stages,
            )
        except Exception as exc:
            if force:
                print(f"⚠️  EmbedStage: failed to publish progress update ({exc})", flush=True)

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage], set[Mapping]]
    ):
        old_pages, new_pages, mappings = input

        # Filter out pages with empty or very short HTML (scraping failures)
        MIN_HTML_LENGTH = 100
        valid_old_pages = [p for p in old_pages if len(p.html) >= MIN_HTML_LENGTH]
        valid_new_pages = [p for p in new_pages if len(p.html) >= MIN_HTML_LENGTH]

        skipped_old = len(old_pages) - len(valid_old_pages)
        skipped_new = len(new_pages) - len(valid_new_pages)

        print(f"\nEmbedStage: Filtering pages with HTML < {MIN_HTML_LENGTH} bytes...", flush=True)
        if skipped_old > 0:
            print(f"⚠️  EmbedStage: Skipping {skipped_old} old pages (failed to scrape):", flush=True)
            for page in old_pages:
                if len(page.html) < MIN_HTML_LENGTH:
                    print(f"   - {page.url}", flush=True)

        if skipped_new > 0:
            print(f"⚠️  EmbedStage: Skipping {skipped_new} new pages (failed to scrape):", flush=True)
            for page in new_pages:
                if len(page.html) < MIN_HTML_LENGTH:
                    print(f"   - {page.url}", flush=True)

        print(f"EmbedStage: Generating embeddings for {len(valid_old_pages)} old + {len(valid_new_pages)} new pages...", flush=True)

        # Validate OpenAI config
        Config.validate_embeddings()

        # Create session if needed
        if self.session_id is None:
            self.session_id = self.session_db.create_session(user_id="default")

        # Create OpenAI client
        self.openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        total_success = 0
        total_failure = 0
        expected_count = len(valid_old_pages) + len(valid_new_pages)
        generated_so_far = 0
        failed_so_far = 0
        self._last_reported_embeddings = -1

        self._publish_embedding_progress(
            generated_count=0,
            target_count=expected_count,
            failed_count=0,
            force=True,
        )

        def on_batch_complete(batch_success: int, batch_failure: int) -> None:
            nonlocal generated_so_far, failed_so_far
            generated_so_far += batch_success
            failed_so_far += batch_failure
            self._publish_embedding_progress(
                generated_count=generated_so_far,
                target_count=expected_count,
                failed_count=failed_so_far,
            )

        try:
            if valid_old_pages:
                success, failure = await self._process_pages(
                    valid_old_pages,
                    "old",
                    on_batch_complete=on_batch_complete,
                )
                total_success += success
                total_failure += failure
            if valid_new_pages:
                success, failure = await self._process_pages(
                    valid_new_pages,
                    "new",
                    on_batch_complete=on_batch_complete,
                )
                total_success += success
                total_failure += failure
        finally:
            if self.openai_client:
                await self.openai_client.close()

        self._publish_embedding_progress(
            generated_count=total_success,
            target_count=expected_count,
            failed_count=total_failure,
            force=True,
        )
        print(f"EmbedStage: Successfully generated {total_success}/{expected_count} embeddings", flush=True)

        if total_failure > 0:
            error_msg = f"Failed to generate {total_failure} embeddings"
            print(f"❌ EmbedStage: {error_msg}", flush=True)
            raise Exception(error_msg)

        return input

    async def _process_pages(
        self,
        pages: list[WebPage],
        site_type: str,
        on_batch_complete: Optional[Callable[[int, int], None]] = None,
    ):
        batch_size = 10
        success_count = 0
        failure_count = 0

        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            results = await asyncio.gather(
                *[self._generate_and_store_embedding(page, site_type) for page in batch],
                return_exceptions=True
            )

            # Count successes and failures
            batch_success = 0
            batch_failure = 0
            for result in results:
                if isinstance(result, Exception):
                    failure_count += 1
                    batch_failure += 1
                elif result:
                    success_count += 1
                    batch_success += 1
                else:
                    failure_count += 1
                    batch_failure += 1

            if on_batch_complete:
                on_batch_complete(batch_success, batch_failure)

        if failure_count > 0:
            print(f"⚠️  EmbedStage: {failure_count} pages failed to embed")

        return success_count, failure_count

    async def _generate_and_store_embedding(self, page: WebPage, site_type: str) -> bool:
        """
        Generate and store embedding for a page.

        Returns:
            True if successful, False if failed
        """
        try:
            text = page.extract_text()
            title = page.extract_title()
            embedding = await self._generate_embedding_with_retry(text)

            self.embedding_db.insert_embedding(
                session_id=self.session_id,
                url=page.url,
                site_type=site_type,
                embedding=embedding,
                extracted_text=text,
                title=title
            )
            return True
        except Exception as e:
            print(f"⚠️  Error embedding {page.url}: {e}")
            return False

    async def _generate_embedding_with_retry(self, text: str, max_retries=3):
        for attempt in range(max_retries):
            try:
                resp = await self.openai_client.embeddings.create(
                    input=text,
                    model=Config.EMBEDDING_MODEL,
                    encoding_format="float"
                )
                return np.array(resp.data[0].embedding, dtype=np.float32)

            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

class PairingStage(Stage):
    """
    Pairs old and new webpages using vector similarity search.
    Generates redirect mappings with confidence scores and review flags.
    """
    name = "Pairing URLs"

    def __init__(self, session_id: Optional[UUID] = None):
        """
        Initialize the PairingStage.

        Args:
            session_id: Migration session ID (required for database operations).
        """
        super().__init__()
        self.session_id = session_id
        self.embedding_db = WebPageEmbeddingDB()
        self.mapping_db = URLMappingDB()

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage], set[Mapping]]
    ) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:
        """
        Generate URL mappings using vector similarity search.

        Args:
            input: Tuple of (old_pages, new_pages, existing_mappings) from HtmlPruneStage.

        Returns:
            Same tuple with updated mappings set.

        Raises:
            ValueError: If session_id is not set.
        """
        old_pages, new_pages, existing_mappings = input

        if self.session_id is None:
            raise ValueError("session_id must be set before executing PairingStage")

        # Track which pages have been matched
        matched_old_pages = set()
        matched_new_pages = set()
        matched_new_urls = set()
        all_mappings = set(existing_mappings)

        # First, process existing mappings from HtmlPruneStage (exact HTML matches)
        print(f"Processing {len(existing_mappings)} exact HTML matches from HtmlPruneStage...")
        for mapping in existing_mappings:
            # Store in database
            self.mapping_db.insert_mapping(
                session_id=self.session_id,
                old_url=mapping.old_page.url,
                new_url=mapping.new_page.url,
                confidence_score=mapping.confidence_score,
                match_type=mapping.match_type,
                needs_review=mapping.needs_review
            )
            matched_old_pages.add(mapping.old_page)
            matched_new_pages.add(mapping.new_page)
            matched_new_urls.add(mapping.new_page.url)

        # Find remaining unmatched pages
        unmatched_old_pages = [p for p in old_pages if p not in matched_old_pages]
        unmatched_new_pages = [p for p in new_pages if p not in matched_new_pages]
        unmatched_new_pages_by_url = {p.url: p for p in unmatched_new_pages}

        # Filter out root paths - homepage doesn't need redirect
        def is_root_path(url: str) -> bool:
            from urllib.parse import urlparse
            try:
                path = urlparse(url).path
                return path in ['/', '/index.html', '/index.htm']
            except:
                return False

        root_pages_old = [p for p in unmatched_old_pages if is_root_path(p.url)]
        unmatched_old_pages = [p for p in unmatched_old_pages if not is_root_path(p.url)]

        if root_pages_old:
            print(f"ℹ️  PairingStage: Skipping {len(root_pages_old)} root page(s) - homepage doesn't need redirect")
            for page in root_pages_old:
                print(f"   Orphaned: {page.url} (root path)")

        print(f"Finding semantic matches for {len(unmatched_old_pages)} unmatched old pages...", flush=True)

        # Fetch all old embeddings once (PERFORMANCE FIX: was fetching inside loop!)
        old_embeddings = self.embedding_db.get_embeddings_by_session(
            session_id=self.session_id,
            site_type='old'
        )

        # Build a lookup dict for fast access
        old_embeddings_by_url = {e['url']: e for e in old_embeddings}

        # Process each unmatched old page
        total_unmatched = len(unmatched_old_pages)
        matched_count = 0
        orphaned_count = 0

        for idx, old_page in enumerate(unmatched_old_pages, 1):
            # Find the embedding for this specific old page
            old_embedding_record = old_embeddings_by_url.get(old_page.url)

            if not old_embedding_record:
                print(f"⚠️  [{idx}/{total_unmatched}] No embedding found for {old_page.url}")
                orphaned_count += 1
                continue

            # Progress indicator every 10 pages
            if idx % 10 == 0:
                print(f"   Progress: {idx}/{total_unmatched} pages processed ({matched_count} matched, {orphaned_count} orphaned)")

            old_embedding = np.array(old_embedding_record['embedding'], dtype=np.float32)

            # Find similar pages in new site (excluding already matched pages)
            similar_pages = self.embedding_db.find_similar_pages(
                query_embedding=old_embedding,
                session_id=self.session_id,
                site_type='new',
                match_count=5,  # Get top 5 candidates
                match_threshold=0.0  # We'll filter by threshold ourselves
            )

            # Filter out already matched pages and root paths
            similar_pages = [
                p for p in similar_pages
                if p['url'] not in matched_new_urls
                and not is_root_path(p['url'])  # Don't redirect TO root/homepage
            ]

            if not similar_pages:
                orphaned_count += 1
                if idx <= 5 or idx % 20 == 0:  # Only print first 5 and every 20th to reduce spam
                    print(f"   [{idx}/{total_unmatched}] {old_page.url} -> No match found (orphaned)")
                continue

            # Find best match among candidates
            best_match = self._find_best_match(similar_pages)

            if best_match:
                # Find the corresponding WebPage object
                new_page = unmatched_new_pages_by_url.get(best_match['url'])

                if new_page:
                    # Create mapping with confidence scoring
                    mapping = self._create_mapping(
                        old_page=old_page,
                        new_page=new_page,
                        similarity_score=best_match['similarity'],
                        similar_pages=similar_pages
                    )

                    # Store in database
                    self.mapping_db.insert_mapping(
                        session_id=self.session_id,
                        old_url=mapping.old_page.url,
                        new_url=mapping.new_page.url,
                        confidence_score=mapping.confidence_score,
                        match_type=mapping.match_type,
                        needs_review=mapping.needs_review
                    )

                    # Track this mapping
                    all_mappings.add(mapping)
                    matched_old_pages.add(old_page)
                    matched_new_pages.add(new_page)
                    matched_new_urls.add(new_page.url)
                    unmatched_new_pages_by_url.pop(new_page.url, None)
                    matched_count += 1

                    review_flag = " [NEEDS REVIEW]" if mapping.needs_review else ""
                    print(f"✓ [{idx}/{total_unmatched}] {old_page.url} -> {new_page.url} "
                          f"(score: {mapping.confidence_score:.3f}, type: {mapping.match_type}){review_flag}")

        # Identify orphaned pages (old pages with no match, excluding root paths)
        final_orphaned = [p for p in old_pages if p not in matched_old_pages and not is_root_path(p.url)]
        if final_orphaned:
            print(f"\nOrphaned pages (no suitable match found):")
            for page in final_orphaned:
                print(f"  - {page.url}")

        # Identify new pages (new pages with no old equivalent)
        final_new = [p for p in new_pages if p not in matched_new_pages]
        if final_new:
            print(f"\nNew pages (no old equivalent):")
            for page in final_new:
                print(f"  - {page.url}")

        # Summary
        print(f"\n=== Pairing Summary ===")
        print(f"Total mappings: {len(all_mappings)}")
        print(f"  - Exact HTML matches: {len(existing_mappings)}")
        print(f"  - Semantic matches: {len(all_mappings) - len(existing_mappings)}")
        print(f"Orphaned old pages: {len(final_orphaned)}")
        print(f"New pages: {len(final_new)}")

        # Return input unchanged (mappings stored in database)
        return (old_pages, new_pages, all_mappings)

    def _find_best_match(self, similar_pages: list[dict]) -> Optional[dict]:
        """
        Find the best match from a list of similar pages.
        Returns None if no page meets the minimum threshold.

        Args:
            similar_pages: List of similar pages with similarity scores.

        Returns:
            Best matching page or None.
        """
        if not similar_pages:
            return None

        # Filter by minimum threshold (0.6)
        valid_matches = [p for p in similar_pages if p['similarity'] >= Config.MEDIUM_CONFIDENCE_THRESHOLD]

        if not valid_matches:
            return None

        # Return the highest scoring match
        return max(valid_matches, key=lambda p: p['similarity'])

    def _create_mapping(
        self,
        old_page: WebPage,
        new_page: WebPage,
        similarity_score: float,
        similar_pages: list[dict]
    ) -> Mapping:
        """
        Create a Mapping with appropriate confidence scoring and review flags.

        Args:
            old_page: Old site page.
            new_page: New site page.
            similarity_score: Similarity score for this match.
            similar_pages: All similar pages (for ambiguity detection).

        Returns:
            Mapping object with confidence score and metadata.
        """
        # Determine match type based on similarity score
        if similarity_score >= 0.9:
            match_type = 'semantic_high'
            needs_review = False
        elif similarity_score >= Config.HIGH_CONFIDENCE_THRESHOLD:
            match_type = 'semantic_medium'
            # Check for ambiguity
            needs_review = self._is_ambiguous(similarity_score, similar_pages)
        elif similarity_score >= Config.MEDIUM_CONFIDENCE_THRESHOLD:
            match_type = 'semantic_low'
            needs_review = True  # Always review low confidence matches
        else:
            # This shouldn't happen if _find_best_match works correctly
            match_type = 'semantic_very_low'
            needs_review = True

        return Mapping(
            old_page=old_page,
            new_page=new_page,
            confidence_score=similarity_score,
            match_type=match_type,
            needs_review=needs_review
        )

    def _is_ambiguous(self, top_score: float, similar_pages: list[dict]) -> bool:
        """
        Check if the match is ambiguous (top 2 scores are very close).

        Args:
            top_score: The highest similarity score.
            similar_pages: All similar pages with scores.

        Returns:
            True if ambiguous (needs review).
        """
        if len(similar_pages) < 2:
            return False

        # Sort by similarity descending
        sorted_pages = sorted(similar_pages, key=lambda p: p['similarity'], reverse=True)

        if len(sorted_pages) >= 2:
            second_score = sorted_pages[1]['similarity']
            gap = top_score - second_score

            # If gap is less than threshold, it's ambiguous
            return gap < Config.AMBIGUITY_GAP_THRESHOLD

        return False


# =========================
# Helper Classes
# =========================

class Mapping:
    """
    Represents a mapping between an old page and a new page.
    Used to track redirect relationships with confidence scoring.
    """
    def __init__(
        self,
        old_page: WebPage,
        new_page: WebPage,
        confidence_score: float = 1.0,
        match_type: str = 'exact_html',
        needs_review: bool = False
    ):
        """
        Initialize a mapping between two pages.

        Args:
            old_page: WebPage from the old site.
            new_page: WebPage from the new site.
            confidence_score: Similarity score (0.0 to 1.0).
            match_type: Type of match ('exact_html', 'semantic_high', 'semantic_medium', 'semantic_low').
            needs_review: Whether this mapping should be reviewed by a human.
        """
        self.old_page = old_page
        self.new_page = new_page
        self.confidence_score = confidence_score
        self.match_type = match_type
        self.needs_review = needs_review

    def __hash__(self) -> int:
        """Hash based on the old and new page combination."""
        return hash(self.old_page) ^ hash(self.new_page)

    def __eq__(self, other) -> bool:
        """Equality based on old and new page combination."""
        if not isinstance(other, Mapping):
            return False
        return self.old_page == other.old_page and self.new_page == other.new_page

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (f"Mapping({self.old_page.url} -> {self.new_page.url}, "
                f"score={self.confidence_score:.3f}, type={self.match_type}, "
                f"needs_review={self.needs_review})")


class WebPage:
    def __init__(self, url: str, html: str):
        self.url = url
        self.html = html
        self.__html_cache = None
        self._extracted_text = None
        self._title = None

    @staticmethod
    async def scrape(session: aiohttp.ClientSession, url: str, max_retries: int = 3) -> WebPage:
        """
        Scrape a URL with retry logic and exponential backoff.

        Args:
            session: aiohttp ClientSession for making requests
            url: URL to scrape
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            WebPage object with URL and HTML content (empty if scraping fails)
        """
        html = ""
        last_error = None

        for attempt in range(max_retries):
            try:
                # Add small delay between retries (exponential backoff)
                if attempt > 0:
                    delay = 0.5 * (2 ** (attempt - 1))  # 0.5s, 1s, 2s
                    await asyncio.sleep(delay)

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        html = await response.text()
                        return WebPage(url, html)
                    else:
                        last_error = f"Status {response.status}"
                        if attempt == max_retries - 1:
                            print(f"Warning: Failed to scrape {url} - {last_error} (after {max_retries} attempts)")

            except aiohttp.ClientError as e:
                last_error = f"Connection error: {e}"
                if attempt == max_retries - 1:
                    print(f"Warning: {last_error} scraping {url} (after {max_retries} attempts)")

            except asyncio.TimeoutError:
                last_error = "Timeout"
                if attempt == max_retries - 1:
                    print(f"Warning: Timeout scraping {url} (after {max_retries} attempts)")

            except Exception as e:
                last_error = f"Unexpected error: {e}"
                if attempt == max_retries - 1:
                    print(f"Warning: {last_error} scraping {url} (after {max_retries} attempts)")

        return WebPage(url, html)

    def extract_text(self) -> str:
        if self._extracted_text is not None:
            return self._extracted_text

        try:
            soup = BeautifulSoup(self.html, "lxml")

            # Remove scripts/styles/nav/etc.
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
                tag.decompose()

            main = soup.find("main") or soup.find("article") or soup.find("body") or soup
            text = main.get_text(" ", strip=True)

            text = " ".join(text.split())

            if len(text) > 32000:
                text = text[:32000]

            if len(text) < 10:
                text = self.url

            self._extracted_text = text
            return text

        except Exception:
            self._extracted_text = self.url
            return self.url

    def extract_title(self) -> str:
        if self._title is not None:
            return self._title

        try:
            soup = BeautifulSoup(self.html, "lxml")
            title = soup.find("title")
            if title and title.string:
                self._title = title.string.strip()
                return self._title

            h1 = soup.find("h1")
            if h1:
                self._title = h1.get_text(strip=True)
                return self._title

            self._title = ""
            return ""
        except Exception:
            self._title = ""
            return ""

    def __hash__(self):
        """
        Hash based on HTML content for detecting duplicate content across different URLs.
        Note: Empty or very short HTML is filtered out before hashing in HtmlPruneStage.
        """
        if self.__html_cache is None:
            self.__html_cache = hash(self.html)
        return self.__html_cache

    def __eq__(self, other):
        """
        Equality based on HTML content.
        This allows detecting renamed pages with identical content.
        """
        if not isinstance(other, WebPage):
            return False
        return self.html == other.html
