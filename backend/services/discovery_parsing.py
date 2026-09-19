"""Extract discovery URLs without retaining complete HTML or XML document trees."""
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

CHUNK_CHARACTERS = 65536


def crawl_links(text):
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                href = dict(attrs).get('href')
                if href is not None:
                    found.append(href)
    found = []
    parser = Links(convert_charrefs=True)
    for offset in range(0, len(text), CHUNK_CHARACTERS):
        parser.feed(text[offset:offset + CHUNK_CHARACTERS])
        yield from found
        found.clear()
    parser.close()
    yield from found


def sitemap_urls(text):
    """Yield root kind followed by each direct sitemap/url loc, clearing nodes.

    The consumer must finish iteration before mutating durable checkpoint state,
    so malformed XML at the tail cannot publish earlier partial parse results.
    """
    parser = ET.XMLPullParser(events=('start', 'end'))
    depth = 0
    root = None
    record_kind = None
    loc_seen = False
    loc = None
    def events():
        nonlocal depth, root, record_kind, loc_seen, loc
        for event, element in parser.read_events():
            tag = element.tag.rsplit('}', 1)[-1]
            if event == 'start':
                depth += 1
                if depth == 1:
                    root = element
                    if tag not in ('sitemapindex', 'urlset'):
                        raise ValueError('invalid_sitemap')
                    yield tag
                elif depth == 2:
                    record_kind, loc_seen, loc = tag, False, None
            else:
                if depth == 3 and tag == 'loc' and not loc_seen:
                    loc_seen, loc = True, element.text
                if depth == 2:
                    if record_kind in ('url', 'sitemap'):
                        if not isinstance(loc, str) or not loc.strip():
                            raise ValueError('invalid_sitemap')
                        yield loc.strip()
                    root.clear()
                element.clear()
                depth -= 1
    for offset in range(0, len(text), CHUNK_CHARACTERS):
        parser.feed(text[offset:offset + CHUNK_CHARACTERS])
        yield from events()
    parser.close()
    yield from events()
