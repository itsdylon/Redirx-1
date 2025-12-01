"""
Unit tests for HtmlPruneStage.

Tests matching of pages with identical HTML content via hash comparison.
"""

import unittest
import asyncio
import os
import sys
from pathlib import Path

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import HtmlPruneStage, WebPage, Mapping


class TestHtmlPruneStage(unittest.TestCase):
    """Tests for HtmlPruneStage HTML matching logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.stage = HtmlPruneStage()

    # ========================================================================
    # Execute Tests - Basic Functionality
    # ========================================================================

    async def test_execute_empty_input(self):
        """Test execute with empty page lists."""
        old_pages = []
        new_pages = []

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        self.assertEqual(result_old, [])
        self.assertEqual(result_new, [])
        self.assertEqual(mappings, set())

    async def test_execute_no_matches(self):
        """Test execute when no pages have matching HTML."""
        old_pages = [
            WebPage('http://old.com/page1.html', '<html><body>Old Content 1</body></html>'),
            WebPage('http://old.com/page2.html', '<html><body>Old Content 2</body></html>')
        ]
        new_pages = [
            WebPage('http://new.com/page1.html', '<html><body>New Content 1</body></html>'),
            WebPage('http://new.com/page2.html', '<html><body>New Content 2</body></html>')
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Pages should be unchanged
        self.assertEqual(result_old, old_pages)
        self.assertEqual(result_new, new_pages)

        # No mappings should be created
        self.assertEqual(len(mappings), 0)

    async def test_execute_single_exact_match(self):
        """Test execute with one exact HTML match."""
        identical_html = '<html><body><h1>Same Content</h1><p>Exact match</p></body></html>'

        old_pages = [
            WebPage('http://old.com/page1.html', identical_html),
            WebPage('http://old.com/page2.html', '<html><body>Different</body></html>')
        ]
        new_pages = [
            WebPage('http://new.com/page1-renamed.html', identical_html),
            WebPage('http://new.com/page2.html', '<html><body>Other</body></html>')
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Verify one mapping created
        self.assertEqual(len(mappings), 1)

        # Get the mapping
        mapping = list(mappings)[0]
        self.assertIsInstance(mapping, Mapping)

        # Verify mapping properties
        self.assertEqual(mapping.old_page.url, 'http://old.com/page1.html')
        self.assertEqual(mapping.new_page.url, 'http://new.com/page1-renamed.html')
        self.assertEqual(mapping.confidence_score, 1.0)
        self.assertEqual(mapping.match_type, 'exact_html')
        self.assertFalse(mapping.needs_review)

    async def test_execute_multiple_exact_matches(self):
        """Test execute with multiple exact HTML matches."""
        html1 = '<html><body>Content 1</body></html>'
        html2 = '<html><body>Content 2</body></html>'
        html3 = '<html><body>Content 3</body></html>'

        old_pages = [
            WebPage('http://old.com/a.html', html1),
            WebPage('http://old.com/b.html', html2),
            WebPage('http://old.com/c.html', html3)
        ]
        new_pages = [
            WebPage('http://new.com/alpha.html', html1),
            WebPage('http://new.com/beta.html', html2),
            WebPage('http://new.com/gamma.html', html3)
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Verify all three mappings created
        self.assertEqual(len(mappings), 3)

        # Verify all have correct properties
        for mapping in mappings:
            self.assertEqual(mapping.confidence_score, 1.0)
            self.assertEqual(mapping.match_type, 'exact_html')
            self.assertFalse(mapping.needs_review)

    async def test_execute_partial_matches(self):
        """Test execute with some matches and some non-matches."""
        identical = '<html><body>Identical</body></html>'

        old_pages = [
            WebPage('http://old.com/match.html', identical),
            WebPage('http://old.com/unique-old.html', '<html><body>Only in old</body></html>')
        ]
        new_pages = [
            WebPage('http://new.com/match-renamed.html', identical),
            WebPage('http://new.com/unique-new.html', '<html><body>Only in new</body></html>')
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Only one match should be found
        self.assertEqual(len(mappings), 1)

        mapping = list(mappings)[0]
        self.assertEqual(mapping.old_page.html, identical)
        self.assertEqual(mapping.new_page.html, identical)

    # ========================================================================
    # Execute Tests - Edge Cases
    # ========================================================================

    async def test_execute_whitespace_differences_not_matched(self):
        """Test that pages with only whitespace differences are NOT matched."""
        old_pages = [
            WebPage('http://old.com/page.html', '<html><body>Content</body></html>')
        ]
        new_pages = [
            WebPage('http://new.com/page.html', '<html><body> Content </body></html>')
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Should NOT match (hash is different due to whitespace)
        self.assertEqual(len(mappings), 0)

    async def test_execute_case_sensitive_matching(self):
        """Test that HTML matching is case-sensitive."""
        old_pages = [
            WebPage('http://old.com/page.html', '<html><body>Content</body></html>')
        ]
        new_pages = [
            WebPage('http://new.com/page.html', '<html><body>CONTENT</body></html>')
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Should NOT match (case sensitive)
        self.assertEqual(len(mappings), 0)

    async def test_execute_empty_html_matches(self):
        """Test that two pages with empty HTML are matched."""
        old_pages = [WebPage('http://old.com/empty.html', '')]
        new_pages = [WebPage('http://new.com/empty.html', '')]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Empty HTML should match
        self.assertEqual(len(mappings), 1)

    async def test_execute_one_to_many_match(self):
        """Test that if old page matches multiple new pages, only first is used."""
        identical = '<html><body>Same</body></html>'

        old_pages = [WebPage('http://old.com/page.html', identical)]
        new_pages = [
            WebPage('http://new.com/copy1.html', identical),
            WebPage('http://new.com/copy2.html', identical)
        ]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Only one mapping should be created (dict key collision)
        self.assertEqual(len(mappings), 1)

        # Should match to one of the new pages (which one depends on dict ordering)
        mapping = list(mappings)[0]
        self.assertIn(mapping.new_page.url, ['http://new.com/copy1.html', 'http://new.com/copy2.html'])

    async def test_execute_many_to_one_match(self):
        """Test that if multiple old pages match same new page, all create mappings."""
        identical = '<html><body>Same</body></html>'

        old_pages = [
            WebPage('http://old.com/copy1.html', identical),
            WebPage('http://old.com/copy2.html', identical)
        ]
        new_pages = [WebPage('http://new.com/page.html', identical)]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Both old pages should match to the same new page
        self.assertEqual(len(mappings), 2)

        for mapping in mappings:
            self.assertEqual(mapping.new_page.url, 'http://new.com/page.html')

    async def test_execute_preserves_original_lists(self):
        """Test that input lists are returned unchanged."""
        old_pages = [WebPage('http://old.com/page.html', '<html>Old</html>')]
        new_pages = [WebPage('http://new.com/page.html', '<html>New</html>')]

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Lists should be returned as-is
        self.assertIs(result_old, old_pages)
        self.assertIs(result_new, new_pages)

    # ========================================================================
    # Execute Tests - Performance
    # ========================================================================

    async def test_execute_with_many_pages(self):
        """Test execute with large number of pages (hash lookup performance)."""
        # Create 100 old pages and 100 new pages, with 10 matches
        old_pages = []
        new_pages = []

        for i in range(100):
            old_html = f'<html><body>Old Content {i}</body></html>'
            old_pages.append(WebPage(f'http://old.com/page{i}.html', old_html))

            # Every 10th page has matching HTML
            if i % 10 == 0:
                new_pages.append(WebPage(f'http://new.com/page{i}.html', old_html))
            else:
                new_html = f'<html><body>New Content {i}</body></html>'
                new_pages.append(WebPage(f'http://new.com/page{i}.html', new_html))

        result_old, result_new, mappings = await self.stage.execute((old_pages, new_pages))

        # Should find 10 matches
        self.assertEqual(len(mappings), 10)

    # ========================================================================
    # WebPage Hash Tests
    # ========================================================================

    def test_webpage_hash_consistency(self):
        """Test that WebPage hash is consistent for same HTML."""
        html = '<html><body>Test</body></html>'
        page1 = WebPage('http://example.com/page1.html', html)
        page2 = WebPage('http://example.com/page2.html', html)

        # Same HTML should produce same hash
        self.assertEqual(hash(page1), hash(page2))

    def test_webpage_hash_different_for_different_html(self):
        """Test that different HTML produces different hashes."""
        page1 = WebPage('http://example.com/page.html', '<html><body>A</body></html>')
        page2 = WebPage('http://example.com/page.html', '<html><body>B</body></html>')

        # Different HTML should produce different hashes
        self.assertNotEqual(hash(page1), hash(page2))

    def test_webpage_hash_caching(self):
        """Test that WebPage hash is cached."""
        html = '<html><body>Test</body></html>'
        page = WebPage('http://example.com/page.html', html)

        # First hash
        hash1 = hash(page)

        # Verify __html_cache is set
        self.assertIsNotNone(page._WebPage__html_cache)

        # Second hash (should use cache)
        hash2 = hash(page)

        # Should be same value
        self.assertEqual(hash1, hash2)


# ============================================================================
# Test Runner Helper
# ============================================================================

def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestHtmlPruneStage):
    attr = getattr(TestHtmlPruneStage, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestHtmlPruneStage, attr_name, async_test(attr))