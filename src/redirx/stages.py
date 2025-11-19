import aiohttp
import asyncio

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
    Scrapes all URLs for their HTML content, automatically pairs sites with duplicate content. 
    """
    async def execute(self, input: tuple[list[WebPage], list[WebPage]]) -> tuple[list[WebPage], list[WebPage], set[Mapping]]:
        # TODO
        pass

# TODO
class EmbedStage(Stage):
    pass

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
    
    @classmethod
    async def scrape(session: aiohttp.ClientSession, url: str) -> WebPage:
        async with session.get(url) as response:
            html = await response.text()

        return WebPage(url, html)
    
    # Potential performance improvement by caching hash?
    def __hash__(self) -> int:
        return hash(self.html)