"""Bound raw response, decoded response, and retained pivot content bytes."""
import codecs
import zlib
from bs4 import BeautifulSoup

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 32000
MAX_TITLE_BYTES = 512
CHUNK_BYTES = 16384


class ContentTooLarge(ValueError):
    code = 'content_too_large'


class ContentUnavailable(ValueError):
    code = 'content_unavailable'


class ContentStorageError(RuntimeError):
    """An internal spool failure must fail the job, never become missing content."""


def truncate_utf8(value, maximum):
    return value.encode('utf-8')[:maximum].decode('utf-8',errors='ignore')


async def read_bounded_body(response, maximum=MAX_BODY_BYTES):
    """Read with auto_decompress=False; bound compressed and decoded data.

    aiohttp auto decompression is disabled on pivot requests so a compressed
    bomb cannot inflate an unbounded internal buffer before this limit sees it.
    """
    if response.content_length is not None and response.content_length > maximum:
        raise ContentTooLarge('Response body exceeds the pivot content limit')
    encoding = response.headers.get('Content-Encoding','identity').strip().lower()
    if encoding in ('','identity'):
        decompressor = None
    elif encoding == 'gzip':
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == 'deflate':
        decompressor = zlib.decompressobj()
    else:
        raise ContentUnavailable('Unsupported response content encoding')
    output = bytearray()
    received = 0
    async for chunk in response.content.iter_chunked(CHUNK_BYTES):
        received += len(chunk)
        if received > maximum:
            raise ContentTooLarge('Response body exceeds the pivot content limit')
        if decompressor:
            try:
                chunk = decompressor.decompress(chunk,maximum-len(output)+1)
            except zlib.error as exc:
                raise ContentUnavailable('Invalid compressed response') from exc
        output.extend(chunk)
        if len(output)>maximum or (decompressor and decompressor.unconsumed_tail):
            raise ContentTooLarge('Decoded response exceeds the pivot content limit')
    if decompressor and (not decompressor.eof or decompressor.unused_data):
        raise ContentUnavailable('Incomplete or concatenated compressed response')
    return bytes(output)


async def read_bounded_text(response, maximum=MAX_BODY_BYTES):
    content = await read_bounded_body(response,maximum)
    encoding = response.charset or 'utf-8'
    try: codecs.lookup(encoding)
    except LookupError: encoding = 'utf-8'
    return content.decode(encoding,errors='replace')


def extract_bounded_content(html, url):
    soup = BeautifulSoup(html, 'lxml')
    try:
        title_node = soup.find('title')
        if title_node and title_node.string:
            title = title_node.string.strip()
        else:
            h1 = soup.find('h1')
            title = h1.get_text(strip=True) if h1 else ''
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript']):
            tag.decompose()
        main = soup.find('main') or soup.find('article') or soup.find('body') or soup
        text = ' '.join(main.get_text(' ', strip=True).split())
        if len(text) < 10:
            text = url
        return truncate_utf8(text, MAX_TEXT_BYTES), truncate_utf8(title, MAX_TITLE_BYTES)
    finally:
        # BeautifulSoup trees contain cycles; do not leave large trees retained
        # until a later generation of garbage collection during a long scrape.
        soup.decompose()


class BoundedContentStore:
    """One private temporary spool per pivot pipeline; bounded heap, bounded entries."""
    def __init__(self, memory_bytes=8 * 1024 * 1024):
        import tempfile
        import threading
        self.file = tempfile.SpooledTemporaryFile(max_size=memory_bytes, mode='w+b')
        self.lock = threading.Lock()
        self.bytes_written = 0

    def write_text(self, text):
        payload = truncate_utf8(text, MAX_TEXT_BYTES).encode('utf-8')
        try:
            with self.lock:
                self.file.seek(0, 2)
                offset = self.file.tell()
                self.file.write(payload)
                self.bytes_written += len(payload)
            return offset, len(payload)
        except (OSError, ValueError) as exc:
            raise ContentStorageError('Temporary content storage is unavailable') from exc

    def read_text(self, reference):
        offset, size = reference
        try:
            with self.lock:
                self.file.seek(offset)
                payload = self.file.read(size)
            if len(payload) != size:
                raise ContentStorageError('Temporary content storage is incomplete')
            return payload.decode('utf-8')
        except (OSError, ValueError) as exc:
            raise ContentStorageError('Temporary content storage is unavailable') from exc

    def close(self):
        self.file.close()
