from typing import Any, Dict, List, Set, Tuple
import aiohttp
import asyncio

from redirx.ml.embeddings import HtmlEmbedder
from redirx.ml.matching import HtmlMatcher

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
    async def execute(self, input: tuple[list[str], list[str]]) -> tuple[list[WebPage], list[WebPage]]: # type: ignore
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
    async def execute(self, input: tuple[list[WebPage], list[WebPage]]) -> tuple[list[WebPage], list[WebPage], set[Mapping]]: # type: ignore
        old_pages, new_pages = input
        new_page_set = { hash(page) : page for page in new_pages }
        mappings = set()

        for page in old_pages:
            if page in new_page_set:
                mappings.add(Mapping(page, new_page_set[page]), 100)
        
        return (old_pages, new_pages, mappings)

class EmbedStage(Stage):
    """
    Builds embeddings for the 'old' pages and prepares a matcher.

    Input:
        (old_pages, new_pages, exact_mappings)

    Output:
        (old_pages, new_pages, exact_mappings, embedder, matcher)
    """
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        chunk_size: int = 256,
        chunk_overlap: int = 64,
    ):
        super().__init__()
        self.embedder = HtmlEmbedder(
            model_name=model_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    async def execute(
        self,
        input: tuple[list["WebPage"], list["WebPage"], set["Mapping"]],
    ) -> tuple[list["WebPage"], list["WebPage"], set["Mapping"], HtmlEmbedder, HtmlMatcher]:
        old_pages, new_pages, mappings = input

        corpus_docs = [
            {"html": page.html, "url": page.url}
            for page in old_pages
        ]

        # Embed the old pages as the corpus
        urls, embeddings = self.embedder.embed_corpus(corpus_docs)
        matcher = HtmlMatcher(urls, embeddings)

        return old_pages, new_pages, mappings, self.embedder, matcher

class PairingStage(Stage):
    """
    Uses the matcher to pair each 'new' page with the closest 'old' page.

    Input:
        (old_pages, new_pages, exact_mappings, embedder, matcher)

    Output:
        (
            exact_mappings: set[Mapping],   # from HtmlPruneStage (exact HTML duplicates)
            fuzzy_mappings: set[Mapping],   # from embedding-based matching
        )
    """

    def __init__(self, min_score: float = 0.0):
        """
        min_score: optional cosine similarity threshold to filter low-confidence matches.
        """
        super().__init__()
        self.min_score = min_score

    async def execute(
        self,
        input: tuple[
            list["WebPage"],
            list["WebPage"],
            set["Mapping"],
            HtmlEmbedder,
            HtmlMatcher,
        ],
    ) -> tuple[set["Mapping"], set["Mapping"]]:
        old_pages, new_pages, exact_mappings, embedder, matcher = input

        # URL -> WebPage lookup to build Mapping objects
        old_by_url: dict[str, WebPage] = {page.url: page for page in old_pages}
        new_by_url: dict[str, WebPage] = {page.url: page for page in new_pages}

        # Build docs for matcher
        new_docs = [
            {"html": page.html, "url": page.url}
            for page in new_pages
        ]

        # matcher.match_many returns list of dicts:
        #   { "source_url": new_url, "matched_url": old_url, "score": float }
        fuzzy_results = matcher.match_many(
            new_docs,
            embedder=embedder,
            batch_size=32,
        )

        fuzzy_mappings: set[Mapping] = set()

        for r in fuzzy_results:
            source_url = r["source_url"]
            matched_url = r["matched_url"]
            score = float(r["score"])

            if score < self.min_score:
                continue

            new_page = new_by_url.get(source_url)
            old_page = old_by_url.get(matched_url)
            if new_page is None or old_page is None:
                continue

            # Convert cosine similarity [-1, 1] to [0, 100] confidence
            # Clamp negatives to 0.
            confidence = int(max(min(score, 1.0), 0.0) * 100)

            # Maintain consistent ordering: (old_page, new_page)
            fuzzy_mappings.add(Mapping(old_page, new_page, confidence))

        return exact_mappings, fuzzy_mappings

# Helper Classes (move to separate file?)

class Mapping:
    def __init__(self, webpage_1: WebPage, webpage_2: WebPage, confidence: int): # type: ignore
        self.confidence = max(min(confidence, 100), 0)
        self.pairing = (webpage_1, webpage_2)

    def __hash__(self) -> int:
        return hash(self.pairing[0]) ^ hash(self.pairing[1])

class WebPage:
    def __init__(self, url: str, html: str):
        self.url = url
        self.html = html
        self.__html_cache = None
    
    @classmethod
    async def scrape(session: aiohttp.ClientSession, url: str) -> WebPage: # type: ignore
        async with session.get(url) as response:
            if response.status == 200:
                html = await response.text()

        return WebPage(url, html)
    
    # Potential performance improvement by caching hash?
    def __hash__(self) -> int:
        if self.__html_cache is None:
            self.__html_cache = hash(self.html)

        return self.__html_cache