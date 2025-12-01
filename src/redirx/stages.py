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

    """
    Propagates the input data through the stage.
    """
    async def execute(self, input: any) -> any:
        raise NotImplemented('The stage must overwrite execute.')

# Stages

class UrlPruneStage(Stage):
    def __init__(self):
        super().__init__()
    
    """
    Decide whether the URL should be pruned from the output.
    """
    @classmethod
    def __sanitizer(url: str):
        # TODO Establish sanitizer rules.
        return True

    """
    Removes illegal urls from both lists.
    """
    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[str], list[str]]:
        raw_old_urls, raw_new_urls = input

        sanitized_old_urls = list(filter(raw_old_urls, UrlPruneStage.__sanitizer))
        sanitzed_new_urls = list(filter(raw_new_urls, UrlPruneStage.__sanitizer))
        
        return (sanitized_old_urls, sanitzed_new_urls)

class WebScraperStage(Stage):
    def __init__(self):
        super().__init__()

    """
    Scrapes all URLs for their HTML content.
    """
    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[WebPage], list[WebPage]]:
        old_urls, new_urls = input

        session = aiohttp.ClientSession()
        async with asyncio.TaskGroup() as group:
            old_url_task = group.create_task(asyncio.gather(*[WebPage.scrape(session, url) for url in old_urls]))
            new_url_task = group.create_task(asyncio.gather(*[WebPage.scrape(session, url) for url in new_urls]))
        
        old_webpages = old_url_task.result()
        new_webpages = new_url_task.result()        

        await session.close()
        return (old_webpages, new_webpages)


class HtmlPruneStage(Stage):
    def __init__(self):
        super().__init__()

    """
    Pairs sites with duplicate HTML content.
    """
    async def execute(self, input: tuple[list[WebPage], list[WebPage]]) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:
        old_pages, new_pages = input
        new_page_set = { hash(page) : page for page in new_pages }
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

class EmbedStage(Stage):
    """
    Generate vector embeddings for webpage content using OpenAI's text-embedding-3-small model.
    Stores embeddings in Supabase for later similarity search.
    """

    def __init__(self, session_id: Optional[UUID] = None):
        """
        Initialize the EmbedStage.

        Args:
            session_id: Optional migration session ID. If None, a new session will be created.
        """
        super().__init__()
        self.session_id = session_id
        self.embedding_db = WebPageEmbeddingDB()
        self.session_db = MigrationSessionDB()
        self.openai_client: Optional[AsyncOpenAI] = None

    async def execute(
        self,
        input: tuple[list[WebPage], list[WebPage], set[Mapping]]
    ) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:
        """
        Generate embeddings for all webpages and store in database.

        Args:
            input: Tuple of (old_pages, new_pages, mappings) from HtmlPruneStage.

        Returns:
            Same tuple unchanged (embeddings stored as side effect).

        Raises:
            ValueError: If OpenAI API key is not configured.
        """
        old_pages, new_pages, mappings = input

        # Validate OpenAI configuration
        Config.validate_embeddings()

        # Create session if needed
        if self.session_id is None:
            self.session_id = self.session_db.create_session(user_id='default')

        # Initialize OpenAI client
        self.openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        try:
            # Process old pages
            if old_pages:
                await self._process_pages(old_pages, 'old')

            # Process new pages
            if new_pages:
                await self._process_pages(new_pages, 'new')

        finally:
            # Clean up OpenAI client
            if self.openai_client:
                await self.openai_client.close()

        # Return input unchanged
        return (old_pages, new_pages, mappings)

    async def _process_pages(self, pages: list[WebPage], site_type: str) -> None:
        """
        Process a list of pages by generating embeddings and storing them.

        Args:
            pages: List of WebPage objects to process.
            site_type: Either 'old' or 'new'.
        """
        # Process in batches to avoid overwhelming the API
        batch_size = 10
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i + batch_size]
            await self._process_batch(batch, site_type)

    async def _process_batch(self, batch: list[WebPage], site_type: str) -> None:
        """
        Process a batch of pages concurrently.

        Args:
            batch: List of WebPage objects to process.
            site_type: Either 'old' or 'new'.
        """
        tasks = []
        for page in batch:
            task = self._generate_and_store_embedding(page, site_type)
            tasks.append(task)

        # Process all pages in batch concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _generate_and_store_embedding(self, page: WebPage, site_type: str) -> None:
        """
        Generate embedding for a single page and store it in the database.

        Args:
            page: WebPage object to process.
            site_type: Either 'old' or 'new'.
        """
        try:
            # Extract text content
            text = page.extract_text()
            title = page.extract_title()

            # Generate embedding with retry logic
            embedding = await self._generate_embedding_with_retry(text)

            # Store in database
            self.embedding_db.insert_embedding(
                session_id=self.session_id,
                url=page.url,
                site_type=site_type,
                embedding=embedding,
                extracted_text=text,
                title=title
            )

        except Exception as e:
            # Log error but continue processing other pages
            print(f"Error processing {page.url}: {str(e)}")

    async def _generate_embedding_with_retry(
        self,
        text: str,
        max_retries: int = 3
    ) -> np.ndarray:
        """
        Generate embedding with exponential backoff retry logic.

        Args:
            text: Text content to embed.
            max_retries: Maximum number of retry attempts.

        Returns:
            Numpy array of shape (1536,) containing the embedding.

        Raises:
            Exception: If all retry attempts fail.
        """
        for attempt in range(max_retries):
            try:
                response = await self.openai_client.embeddings.create(
                    input=text,
                    model=Config.EMBEDDING_MODEL,
                    encoding_format="float"
                )

                embedding_list = response.data[0].embedding
                return np.array(embedding_list, dtype=np.float32)

            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 2^attempt seconds
                    wait_time = 2 ** attempt
                    print(f"Retry {attempt + 1}/{max_retries} after {wait_time}s: {str(e)}")
                    await asyncio.sleep(wait_time)
                else:
                    # All retries exhausted
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

# Helper Classes (move to separate file?)

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
        self._extracted_text: Optional[str] = None
        self._title: Optional[str] = None
    
    @classmethod
    async def scrape(session: aiohttp.ClientSession, url: str) -> WebPage:
        async with session.get(url) as response:
            if response.status == 200:
                html = await response.text()

        return WebPage(url, html)

    def extract_text(self) -> str:
        """
        Extract clean text content from HTML using BeautifulSoup.
        Removes scripts, styles, navigation elements and normalizes whitespace.
        Caches the result for performance.

        Returns:
            Cleaned text suitable for embedding generation.
        """
        if self._extracted_text is not None:
            return self._extracted_text

        try:
            soup = BeautifulSoup(self.html, 'lxml')

            # Remove unwanted elements
            for element in soup(['script', 'style', 'noscript', 'nav', 'header', 'footer', 'aside']):
                element.decompose()

            # Remove comments
            for comment in soup.find_all(string=lambda text: isinstance(text, str) and text.strip().startswith('<!--')):
                comment.extract()

            # Extract text from body or main content if available
            main_content = soup.find('main') or soup.find('article') or soup.find('body') or soup
            text = main_content.get_text(separator=' ', strip=True)

            # Normalize whitespace
            text = ' '.join(text.split())

            # Limit to reasonable length (~8000 tokens ≈ 32k chars)
            if len(text) > 32000:
                text = text[:32000]

            # Fallback to URL if text is too short
            if len(text) < 10:
                text = self.url

            self._extracted_text = text
            return text

        except Exception as e:
            # If extraction fails, use URL as fallback
            self._extracted_text = self.url
            return self.url

    def extract_title(self) -> str:
        """
        Extract page title from HTML.
        Falls back to first h1 tag if no title tag exists.
        Caches the result for performance.

        Returns:
            Page title or empty string if not found.
        """
        if self._title is not None:
            return self._title

        try:
            soup = BeautifulSoup(self.html, 'lxml')

            # Try <title> tag first
            title_tag = soup.find('title')
            if title_tag and title_tag.string:
                self._title = title_tag.string.strip()
                return self._title

            # Fallback to first <h1>
            h1_tag = soup.find('h1')
            if h1_tag:
                self._title = h1_tag.get_text(strip=True)
                return self._title

            # No title found
            self._title = ''
            return ''

        except Exception:
            self._title = ''
            return ''

    # Potential performance improvement by caching hash?
    def __hash__(self) -> int:
        if self.__html_cache is None:
            self.__html_cache = hash(self.html)

        return self.__html_cache