from __future__ import annotations

import aiohttp
import asyncio
from bs4 import BeautifulSoup
from typing import Optional
from uuid import UUID, uuid4
import numpy as np
from openai import AsyncOpenAI

from .config import Config
from .database import WebPageEmbeddingDB, MigrationSessionDB, URLMappingDB

class Stage:
    def __init__(self):
        pass

    async def execute(self, input: any) -> any:
        raise NotImplementedError("The stage must overwrite execute.")
# =========================
# URL Prune Stage
# =========================

class UrlPruneStage(Stage):
    def __init__(self):
        super().__init__()

    @staticmethod
    def _sanitizer(url: str):
        # TODO: add real rules
        return True

    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        raw_old_urls, raw_new_urls = input

        sanitized_old_urls = [url for url in raw_old_urls if UrlPruneStage._sanitizer(url)]
        sanitized_new_urls = [url for url in raw_new_urls if UrlPruneStage._sanitizer(url)]

        return (sanitized_old_urls, sanitized_new_urls)


# =========================
# Web Scraper Stage
# =========================

class WebScraperStage(Stage):
    def __init__(self):
        super().__init__()

    """
    Scrapes all URLs for their HTML content.
    """
    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[WebPage], list[WebPage]]:
        old_urls, new_urls = input

        async with aiohttp.ClientSession() as session:

            async def gather_old():
                return await asyncio.gather(
                    *[WebPage.scrape(session, url) for url in old_urls]
                )

            async def gather_new():
                return await asyncio.gather(
                    *[WebPage.scrape(session, url) for url in new_urls]
                )

            async with asyncio.TaskGroup() as group:
                old_task = group.create_task(gather_old())
                new_task = group.create_task(gather_new())

            old_webpages = old_task.result()
            new_webpages = new_task.result()

        return (old_webpages, new_webpages)

# =========================
# HTML Prune Stage
# =========================

class HtmlPruneStage(Stage):
    def __init__(self):
        super().__init__()

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage]]
    ) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:

        old_pages, new_pages = input
        new_page_map = {hash(page): page for page in new_pages}
        mappings = set()

        for page in old_pages:
            if hash(page) in new_page_set:
                # Exact HTML match - highest confidence, no review needed
                mappings.add(Mapping(
                    old_page=page,
                    new_page=new_page_set[hash(page)],
                    confidence_score=1.0,
                    match_type='exact_html',
                    needs_review=False
                ))

        return (old_pages, new_pages, mappings)


# =========================
# Embedding Stage
# =========================

class EmbedStage(Stage):
    def __init__(self, session_id: Optional[UUID] = None):
        super().__init__()
        self.session_id = session_id
        self.embedding_db = WebPageEmbeddingDB()
        self.session_db = MigrationSessionDB()
        self.openai_client: Optional[AsyncOpenAI] = None

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage], set[Mapping]]
    ):
        old_pages, new_pages, mappings = input

        # Validate OpenAI config
        Config.validate_embeddings()

        # Create session if needed
        if self.session_id is None:
            self.session_id = self.session_db.create_session(user_id="default")

        # Create OpenAI client
        self.openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        try:
            if old_pages:
                await self._process_pages(old_pages, "old")
            if new_pages:
                await self._process_pages(new_pages, "new")
        finally:
            if self.openai_client:
                await self.openai_client.close()

        return input

    async def _process_pages(self, pages: list[WebPage], site_type: str):
        batch_size = 10
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            await asyncio.gather(
                *[self._generate_and_store_embedding(page, site_type) for page in batch]
            )

    async def _generate_and_store_embedding(self, page: WebPage, site_type: str):
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
        except Exception as e:
            print(f"Error embedding {page.url}: {e}")

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

        # Find remaining unmatched pages
        unmatched_old_pages = [p for p in old_pages if p not in matched_old_pages]
        unmatched_new_pages = [p for p in new_pages if p not in matched_new_pages]

        print(f"Finding semantic matches for {len(unmatched_old_pages)} unmatched old pages...")

        # Process each unmatched old page
        for old_page in unmatched_old_pages:
            # Get embedding for this old page
            old_embeddings = self.embedding_db.get_embeddings_by_session(
                session_id=self.session_id,
                site_type='old'
            )

            # Find the embedding for this specific old page
            old_embedding_record = next(
                (e for e in old_embeddings if e['url'] == old_page.url),
                None
            )

            if not old_embedding_record:
                print(f"Warning: No embedding found for {old_page.url}")
                continue

            old_embedding = np.array(old_embedding_record['embedding'], dtype=np.float32)

            # Find similar pages in new site (excluding already matched pages)
            similar_pages = self.embedding_db.find_similar_pages(
                query_embedding=old_embedding,
                session_id=self.session_id,
                site_type='new',
                match_count=5,  # Get top 5 candidates
                match_threshold=0.0  # We'll filter by threshold ourselves
            )

            # Filter out already matched pages
            similar_pages = [
                p for p in similar_pages
                if not any(p['url'] == matched.url for matched in matched_new_pages)
            ]

            if not similar_pages:
                print(f"No unmatched similar pages found for {old_page.url} (orphaned)")
                continue

            # Find best match among candidates
            best_match = self._find_best_match(similar_pages)

            if best_match:
                # Find the corresponding WebPage object
                new_page = next(
                    (p for p in unmatched_new_pages if p.url == best_match['url']),
                    None
                )

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

                    review_flag = " [NEEDS REVIEW]" if mapping.needs_review else ""
                    print(f"Matched: {old_page.url} -> {new_page.url} "
                          f"(score: {mapping.confidence_score:.3f}, type: {mapping.match_type}){review_flag}")

        # Identify orphaned pages (old pages with no match)
        final_orphaned = [p for p in old_pages if p not in matched_old_pages]
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
    async def scrape(session: aiohttp.ClientSession, url: str) -> WebPage:
        async with session.get(url) as response:
            if response.status == 200:
                html = await response.text()
            else:
                html = ""
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
        if self.__html_cache is None:
            self.__html_cache = hash(self.html)
        return self.__html_cache
