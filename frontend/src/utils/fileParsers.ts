import * as XLSX from 'xlsx';

export interface ParseResult {
  urls: string[];
  warnings: string[];
  errors: string[];
}

/**
 * Checks if a string looks like a URL (absolute or relative path)
 */
export function looksLikeUrl(value: string): boolean {
  if (!value) return false;
  return /^https?:\/\//i.test(value) || value.startsWith('/');
}

/**
 * Parses an XML sitemap and extracts URLs from <loc> elements.
 */
export function parseXmlSitemap(xmlText: string): ParseResult {
  const result: ParseResult = { urls: [], warnings: [], errors: [] };

  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, 'application/xml');

  // Check for XML parse errors
  const parseError = doc.querySelector('parsererror');
  if (parseError) {
    result.errors.push('File is not valid XML.');
    return result;
  }

  const root = doc.documentElement;

  // Detect sitemap index files
  if (root.localName === 'sitemapindex') {
    result.errors.push(
      'This is a sitemap index file, not a sitemap. Please upload an individual sitemap (e.g., sitemap-posts.xml) instead.'
    );
    return result;
  }

  // Must be a <urlset> root
  if (root.localName !== 'urlset') {
    result.errors.push(
      'File is not a sitemap. Expected a <urlset> root element but found <' + root.localName + '>.'
    );
    return result;
  }

  // Extract <loc> elements — try with namespace first, fallback to no namespace
  const NS = 'http://www.sitemaps.org/schemas/sitemap/0.9';
  let locs = doc.getElementsByTagNameNS(NS, 'loc');
  if (locs.length === 0) {
    locs = doc.getElementsByTagName('loc');
  }

  if (locs.length === 0) {
    result.errors.push('Sitemap contains no <loc> elements (no URLs found).');
    return result;
  }

  for (let i = 0; i < locs.length; i++) {
    const url = locs[i].textContent?.trim();
    if (url) {
      result.urls.push(url);
    }
  }

  if (result.urls.length === 0) {
    result.errors.push('All <loc> elements in the sitemap are empty.');
    return result;
  }

  return result;
}

/**
 * Parses an XLSX file and extracts URLs from the first column of the first sheet.
 */
export async function parseXlsx(file: File): Promise<ParseResult> {
  const result: ParseResult = { urls: [], warnings: [], errors: [] };

  const buffer = await file.arrayBuffer();
  const workbook = XLSX.read(buffer, { type: 'array' });

  if (workbook.SheetNames.length === 0) {
    result.errors.push('Excel file contains no sheets.');
    return result;
  }

  if (workbook.SheetNames.length > 1) {
    result.warnings.push(
      `Excel file has ${workbook.SheetNames.length} sheets. Only the first sheet ("${workbook.SheetNames[0]}") will be used.`
    );
  }

  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  const rows: string[][] = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' });

  if (rows.length === 0) {
    result.errors.push('Sheet is empty.');
    return result;
  }

  // Check if first row is a header
  const HEADER_NAMES = /^(url|address|link|page|path|location|source|destination)s?$/i;
  let startRow = 0;
  const firstCell = String(rows[0][0] ?? '').trim();
  if (HEADER_NAMES.test(firstCell)) {
    startRow = 1;
  }

  // Extract first column values
  const values: string[] = [];
  for (let i = startRow; i < rows.length; i++) {
    const cell = String(rows[i][0] ?? '').trim();
    if (cell) {
      values.push(cell);
    }
  }

  if (values.length === 0) {
    result.errors.push('No data found in the first column of the sheet.');
    return result;
  }

  // Sample-check for URLs
  const sampleSize = Math.min(20, values.length);
  let nonUrlCount = 0;
  for (let i = 0; i < sampleSize; i++) {
    if (!looksLikeUrl(values[i])) {
      nonUrlCount++;
    }
  }

  if (nonUrlCount === sampleSize) {
    result.errors.push(
      'First column does not appear to contain URLs. Each row should contain a URL (e.g., https://example.com/page or /page).'
    );
    return result;
  }

  if (nonUrlCount > 0) {
    result.warnings.push(
      `${nonUrlCount} of the first ${sampleSize} rows don't appear to be valid URLs.`
    );
  }

  result.urls = values;
  return result;
}

/**
 * Converts a list of URLs into a .txt File object (one URL per line).
 */
export function urlsToTxtFile(urls: string[], originalFileName: string): File {
  const content = urls.join('\n');
  const blob = new Blob([content], { type: 'text/plain' });
  const baseName = originalFileName.replace(/\.[^.]+$/, '');
  return new File([blob], baseName + '.txt', { type: 'text/plain' });
}
