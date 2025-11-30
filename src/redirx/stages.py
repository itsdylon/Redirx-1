from __future__ import annotations

import aiohttp
import asyncio
from bs4 import BeautifulSoup
from typing import Optional
from uuid import UUID, uuid4
import numpy as np
from openai import AsyncOpenAI

from .config import Config
from .database import WebPageEmbeddingDB, MigrationSessionDB

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
            if page in new_page_set:
                mappings.add(Mapping(page, new_page_set[page]))
        
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

# TODO
class PairingStage(Stage):
    pass

# Helper Classes (move to separate file?)

class Mapping:
    def __init__(self, webpage_1: WebPage, webpage_2: WebPage):
        self.pairing = (webpage_1, webpage_2)

    def __hash__(self) -> int:
        return hash(self.pairing[0]) ^ hash(self.pairing[1])

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