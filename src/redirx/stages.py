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


# =========================
# Base Stage Class
# =========================

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
            if hash(page) in new_page_map:
                mappings.add(Mapping(page, new_page_map[hash(page)]))

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


# =========================
# Pairing Stage (future)
# =========================

class PairingStage(Stage):
    pass


# =========================
# Helper Classes
# =========================

class Mapping:
    def __init__(self, page1: WebPage, page2: WebPage):
        self.pairing = (page1, page2)

    def __hash__(self):
        return hash(self.pairing[0]) ^ hash(self.pairing[1])


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
