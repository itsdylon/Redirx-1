/**
 * Unit tests for URL validation
 *
 * Run with: npm test (if configured) or via manual verification
 */

import { validateUrl } from './validation';

// Test cases for URL validation
const testCases = [
  // Valid relative paths
  { url: '/', expected: true, description: 'Root path' },
  { url: '/about', expected: true, description: 'Simple path' },
  { url: '/products/item-123', expected: true, description: 'Nested path with hyphen' },
  { url: '/api/v2/users', expected: true, description: 'Deep nested path' },
  { url: '/search?query=test&page=1', expected: true, description: 'Path with query parameters' },
  { url: '/page#section', expected: true, description: 'Path with fragment' },
  { url: '/path/with_underscore', expected: true, description: 'Path with underscore' },
  { url: '/path/with.dot', expected: true, description: 'Path with dot' },
  { url: '/path/~user', expected: true, description: 'Path with tilde' },
  { url: '/path%20encoded', expected: true, description: 'Path with URL encoding' },

  // Valid absolute URLs
  { url: 'https://example.com', expected: true, description: 'HTTPS URL without path' },
  { url: 'http://example.com', expected: true, description: 'HTTP URL without path' },
  { url: 'https://example.com/path', expected: true, description: 'HTTPS URL with path' },
  { url: 'https://sub.example.com/path', expected: true, description: 'HTTPS URL with subdomain' },
  { url: 'https://example.com:8080/path', expected: true, description: 'HTTPS URL with port' },
  { url: 'https://example.com/path?query=value', expected: true, description: 'HTTPS URL with query' },

  // Invalid URLs
  { url: '', expected: false, description: 'Empty string' },
  { url: '   ', expected: false, description: 'Whitespace only' },
  { url: ' /path', expected: false, description: 'Leading whitespace' },
  { url: '/path ', expected: false, description: 'Trailing whitespace' },
  { url: '/path with spaces', expected: false, description: 'Unencoded spaces in path' },
  { url: '/path//double', expected: false, description: 'Double slashes in path' },
  { url: 'example.com', expected: false, description: 'Missing protocol' },
  { url: 'ftp://example.com', expected: false, description: 'Invalid protocol' },
  { url: 'https://', expected: false, description: 'Protocol without hostname' },
  { url: 'https://example com', expected: false, description: 'Space in URL' },
  { url: 'not-a-url', expected: false, description: 'Plain text' },
  { url: '\\path', expected: false, description: 'Backslash instead of forward slash' },
];

// Run manual tests
console.log('Running URL validation tests...\n');

let passed = 0;
let failed = 0;

testCases.forEach(({ url, expected, description }) => {
  const result = validateUrl(url);
  const success = result.valid === expected;

  if (success) {
    passed++;
    console.log(`✓ PASS: ${description}`);
  } else {
    failed++;
    console.log(`✗ FAIL: ${description}`);
    console.log(`  URL: "${url}"`);
    console.log(`  Expected: ${expected ? 'valid' : 'invalid'}`);
    console.log(`  Got: ${result.valid ? 'valid' : 'invalid'}`);
    if (result.error) {
      console.log(`  Error: ${result.error}`);
    }
    console.log('');
  }
});

console.log(`\nResults: ${passed} passed, ${failed} failed out of ${testCases.length} tests`);

if (failed === 0) {
  console.log('All tests passed! ✓');
} else {
  console.log(`${failed} test(s) failed ✗`);
  process.exit(1);
}
