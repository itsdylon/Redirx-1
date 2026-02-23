import { describe, it, expect } from 'vitest';
import * as XLSX from 'xlsx';
import {
  looksLikeUrl,
  parseXmlSitemap,
  parseXlsx,
  urlsToTxtFile,
} from './fileParsers';

// ---------------------------------------------------------------------------
// Helper: create an XLSX File from an array-of-arrays
// ---------------------------------------------------------------------------
function createXlsxFile(
  data: (string | number)[][],
  fileName = 'test.xlsx',
  sheetNames?: string[]
): File {
  const wb = XLSX.utils.book_new();
  if (sheetNames && sheetNames.length > 1) {
    // Multi-sheet workbook
    for (let i = 0; i < sheetNames.length; i++) {
      const sheetData = i === 0 ? data : [['placeholder']];
      const ws = XLSX.utils.aoa_to_sheet(sheetData);
      XLSX.utils.book_append_sheet(wb, ws, sheetNames[i]);
    }
  } else {
    const ws = XLSX.utils.aoa_to_sheet(data);
    XLSX.utils.book_append_sheet(wb, ws, sheetNames?.[0] ?? 'Sheet1');
  }
  const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' });
  return new File([buf], fileName, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

// ===========================================================================
// looksLikeUrl
// ===========================================================================
describe('looksLikeUrl', () => {
  it('returns true for https URLs', () => {
    expect(looksLikeUrl('https://example.com')).toBe(true);
    expect(looksLikeUrl('https://example.com/path/to/page')).toBe(true);
  });

  it('returns true for http URLs', () => {
    expect(looksLikeUrl('http://example.com')).toBe(true);
    expect(looksLikeUrl('http://example.com:8080/page')).toBe(true);
  });

  it('is case-insensitive for protocol', () => {
    expect(looksLikeUrl('HTTP://EXAMPLE.COM')).toBe(true);
    expect(looksLikeUrl('Https://Example.com')).toBe(true);
  });

  it('returns true for relative paths starting with /', () => {
    expect(looksLikeUrl('/')).toBe(true);
    expect(looksLikeUrl('/about')).toBe(true);
    expect(looksLikeUrl('/products/item-123')).toBe(true);
  });

  it('returns false for empty or falsy values', () => {
    expect(looksLikeUrl('')).toBe(false);
    expect(looksLikeUrl(null as unknown as string)).toBe(false);
    expect(looksLikeUrl(undefined as unknown as string)).toBe(false);
  });

  it('returns false for plain text', () => {
    expect(looksLikeUrl('hello world')).toBe(false);
    expect(looksLikeUrl('example.com')).toBe(false);
    expect(looksLikeUrl('not a url')).toBe(false);
  });

  it('returns false for ftp and other protocols', () => {
    expect(looksLikeUrl('ftp://files.example.com')).toBe(false);
    expect(looksLikeUrl('mailto:user@example.com')).toBe(false);
  });

  it('returns false for strings that look like headers', () => {
    expect(looksLikeUrl('URL')).toBe(false);
    expect(looksLikeUrl('Page')).toBe(false);
    expect(looksLikeUrl('Address')).toBe(false);
  });
});

// ===========================================================================
// parseXmlSitemap
// ===========================================================================
describe('parseXmlSitemap', () => {
  const SITEMAP_NS = 'http://www.sitemaps.org/schemas/sitemap/0.9';

  function makeSitemap(urls: string[], withNamespace = true): string {
    const ns = withNamespace ? ` xmlns="${SITEMAP_NS}"` : '';
    const locs = urls.map((u) => `  <url><loc>${u}</loc></url>`).join('\n');
    return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset${ns}>\n${locs}\n</urlset>`;
  }

  it('extracts URLs from a valid sitemap with namespace', () => {
    const urls = [
      'https://example.com/',
      'https://example.com/about',
      'https://example.com/contact',
    ];
    const result = parseXmlSitemap(makeSitemap(urls, true));

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual(urls);
  });

  it('extracts URLs from a valid sitemap without namespace', () => {
    const urls = ['https://example.com/page1', 'https://example.com/page2'];
    const result = parseXmlSitemap(makeSitemap(urls, false));

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual(urls);
  });

  it('handles sitemap with many URLs', () => {
    const urls = Array.from({ length: 500 }, (_, i) => `https://example.com/page-${i}`);
    const result = parseXmlSitemap(makeSitemap(urls));

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toHaveLength(500);
  });

  it('handles sitemap with extra elements (lastmod, changefreq, priority)', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${SITEMAP_NS}">
  <url>
    <loc>https://example.com/</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://example.com/about</loc>
    <lastmod>2024-01-15</lastmod>
  </url>
</urlset>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual([
      'https://example.com/',
      'https://example.com/about',
    ]);
  });

  it('trims whitespace from loc elements', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${SITEMAP_NS}">
  <url><loc>  https://example.com/page  </loc></url>
</urlset>`;
    const result = parseXmlSitemap(xml);

    expect(result.urls).toEqual(['https://example.com/page']);
  });

  it('skips empty loc elements but includes others', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${SITEMAP_NS}">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>  </loc></url>
  <url><loc></loc></url>
  <url><loc>https://example.com/page2</loc></url>
</urlset>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual([
      'https://example.com/page1',
      'https://example.com/page2',
    ]);
  });

  it('errors on sitemap index files', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="${SITEMAP_NS}">
  <sitemap>
    <loc>https://example.com/sitemap-posts.xml</loc>
  </sitemap>
</sitemapindex>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('sitemap index');
    expect(result.urls).toHaveLength(0);
  });

  it('errors on non-sitemap XML (RSS feed)', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item><link>https://example.com/post1</link></item>
  </channel>
</rss>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('not a sitemap');
    expect(result.errors[0]).toContain('<rss>');
  });

  it('errors on non-sitemap XML (HTML document)', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<html><body><p>This is HTML</p></body></html>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('not a sitemap');
  });

  it('errors on malformed XML', () => {
    const result = parseXmlSitemap('<urlset><url><loc>unclosed');

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('not valid XML');
  });

  it('errors on completely invalid content', () => {
    const result = parseXmlSitemap('this is not xml at all {{{ }}');

    expect(result.errors).toHaveLength(1);
  });

  it('errors on empty urlset with no loc elements', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${SITEMAP_NS}">
</urlset>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('no <loc> elements');
  });

  it('errors when all loc elements are empty', () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${SITEMAP_NS}">
  <url><loc></loc></url>
  <url><loc>  </loc></url>
</urlset>`;
    const result = parseXmlSitemap(xml);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('empty');
  });
});

// ===========================================================================
// parseXlsx
// ===========================================================================
describe('parseXlsx', () => {
  it('extracts URLs from first column', async () => {
    const file = createXlsxFile([
      ['https://example.com/page1'],
      ['https://example.com/page2'],
      ['https://example.com/page3'],
    ]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual([
      'https://example.com/page1',
      'https://example.com/page2',
      'https://example.com/page3',
    ]);
  });

  it('extracts relative paths from first column', async () => {
    const file = createXlsxFile([
      ['/about'],
      ['/contact'],
      ['/products/item-1'],
    ]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(0);
    expect(result.urls).toEqual(['/about', '/contact', '/products/item-1']);
  });

  it('ignores columns beyond the first', async () => {
    const file = createXlsxFile([
      ['https://example.com/page1', 'Title 1', 'Description 1'],
      ['https://example.com/page2', 'Title 2', 'Description 2'],
    ]);
    const result = await parseXlsx(file);

    expect(result.urls).toEqual([
      'https://example.com/page1',
      'https://example.com/page2',
    ]);
  });

  it('auto-skips common header rows', async () => {
    const headerNames = ['URL', 'url', 'Urls', 'Link', 'Page', 'Path', 'Address', 'Location', 'Source', 'Destination'];

    for (const header of headerNames) {
      const file = createXlsxFile([
        [header],
        ['https://example.com/page1'],
        ['https://example.com/page2'],
      ]);
      const result = await parseXlsx(file);

      expect(result.urls).toEqual([
        'https://example.com/page1',
        'https://example.com/page2',
      ]);
    }
  });

  it('does not skip a header when first cell is a URL', async () => {
    const file = createXlsxFile([
      ['https://example.com/'],
      ['https://example.com/page1'],
    ]);
    const result = await parseXlsx(file);

    expect(result.urls).toHaveLength(2);
    expect(result.urls[0]).toBe('https://example.com/');
  });

  it('warns when multiple sheets exist', async () => {
    const file = createXlsxFile(
      [['https://example.com/page1'], ['https://example.com/page2']],
      'multi.xlsx',
      ['URLs', 'Metadata', 'Notes']
    );
    const result = await parseXlsx(file);

    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toContain('3 sheets');
    expect(result.warnings[0]).toContain('URLs');
    expect(result.urls).toHaveLength(2);
  });

  it('errors on empty sheet', async () => {
    const file = createXlsxFile([]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('empty');
  });

  it('errors when first column has no data', async () => {
    const file = createXlsxFile([
      [''],
      [''],
      [''],
    ]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('No data found');
  });

  it('errors when content does not look like URLs', async () => {
    const file = createXlsxFile([
      ['Apple'],
      ['Banana'],
      ['Cherry'],
      ['Date'],
      ['Elderberry'],
    ]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('does not appear to contain URLs');
  });

  it('warns when some rows are not URLs', async () => {
    const file = createXlsxFile([
      ['https://example.com/page1'],
      ['not-a-url'],
      ['https://example.com/page3'],
      ['also not a url'],
      ['https://example.com/page5'],
    ]);
    const result = await parseXlsx(file);

    expect(result.errors).toHaveLength(0);
    expect(result.warnings.length).toBeGreaterThan(0);
    expect(result.warnings[0]).toContain("don't appear to be valid URLs");
    // All values are still returned (including non-URLs)
    expect(result.urls).toHaveLength(5);
  });

  it('skips empty rows in the middle', async () => {
    const file = createXlsxFile([
      ['https://example.com/page1'],
      [''],
      ['https://example.com/page3'],
    ]);
    const result = await parseXlsx(file);

    expect(result.urls).toEqual([
      'https://example.com/page1',
      'https://example.com/page3',
    ]);
  });

  it('handles numeric cell values gracefully', async () => {
    const file = createXlsxFile([
      [12345],
      [67890],
    ]);
    const result = await parseXlsx(file);

    // Numbers don't look like URLs, so should error
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toContain('does not appear to contain URLs');
  });
});

// ===========================================================================
// urlsToTxtFile
// ===========================================================================
describe('urlsToTxtFile', () => {
  it('creates a File with correct content', async () => {
    const urls = ['https://example.com/', 'https://example.com/about'];
    const file = urlsToTxtFile(urls, 'sitemap.xml');
    const text = await file.text();

    expect(text).toBe('https://example.com/\nhttps://example.com/about');
  });

  it('replaces the original extension with .txt', () => {
    expect(urlsToTxtFile(['https://a.com'], 'sitemap.xml').name).toBe('sitemap.txt');
    expect(urlsToTxtFile(['https://a.com'], 'urls.xlsx').name).toBe('urls.txt');
    expect(urlsToTxtFile(['https://a.com'], 'data.csv').name).toBe('data.txt');
  });

  it('handles filenames without extension', () => {
    expect(urlsToTxtFile(['https://a.com'], 'noextension').name).toBe('noextension.txt');
  });

  it('creates a file with text/plain type', () => {
    const file = urlsToTxtFile(['https://a.com'], 'test.xml');
    expect(file.type).toBe('text/plain');
  });

  it('handles a large list of URLs', async () => {
    const urls = Array.from({ length: 1000 }, (_, i) => `https://example.com/page-${i}`);
    const file = urlsToTxtFile(urls, 'large.xml');
    const text = await file.text();
    const lines = text.split('\n');

    expect(lines).toHaveLength(1000);
    expect(lines[0]).toBe('https://example.com/page-0');
    expect(lines[999]).toBe('https://example.com/page-999');
  });

  it('handles single URL', async () => {
    const file = urlsToTxtFile(['https://example.com/only'], 'single.xml');
    const text = await file.text();

    expect(text).toBe('https://example.com/only');
  });
});
