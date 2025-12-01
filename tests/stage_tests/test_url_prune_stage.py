"""
Unit tests for UrlPruneStage.

Tests filtering of asset files (CSS, JS, images, etc.) vs HTML pages.
"""

import unittest
import asyncio
import os
import sys

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import UrlPruneStage


class TestUrlPruneStage(unittest.TestCase):
    """Tests for UrlPruneStage filtering logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.stage = UrlPruneStage()

    # ========================================================================
    # Sanitizer Tests - Blocked Extensions
    # ========================================================================

    def test_sanitizer_blocks_css(self):
        """Test that CSS files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/styles.css'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/assets/main.css'))
        self.assertFalse(UrlPruneStage._sanitizer('/assets/styles.css'))

    def test_sanitizer_blocks_js(self):
        """Test that JavaScript files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/app.js'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/assets/main.js'))
        self.assertFalse(UrlPruneStage._sanitizer('/assets/app.js'))

    def test_sanitizer_blocks_images(self):
        """Test that image files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/logo.png'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/photo.jpg'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/image.jpeg'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/icon.gif'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/graphic.svg'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/favicon.ico'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/banner.webp'))

    def test_sanitizer_blocks_fonts(self):
        """Test that font files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/font.woff'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/font.woff2'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/font.ttf'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/font.eot'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/font.otf'))

    def test_sanitizer_blocks_documents(self):
        """Test that document files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/doc.pdf'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/archive.zip'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/data.csv'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/log.txt'))

    def test_sanitizer_blocks_data_files(self):
        """Test that data/config files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/config.json'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/sitemap.xml'))

    def test_sanitizer_blocks_media(self):
        """Test that media files are filtered out."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/video.mp4'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/audio.mp3'))

    # ========================================================================
    # Sanitizer Tests - Allowed URLs
    # ========================================================================

    def test_sanitizer_allows_html(self):
        """Test that HTML files are allowed."""
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/index.html'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/about.html'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/page.htm'))
        self.assertTrue(UrlPruneStage._sanitizer('/about.html'))

    def test_sanitizer_allows_paths_without_extensions(self):
        """Test that URLs without file extensions are allowed (likely pages)."""
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/about'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/blog/post-title'))
        self.assertTrue(UrlPruneStage._sanitizer('/about'))
        self.assertTrue(UrlPruneStage._sanitizer('/blog/post'))

    def test_sanitizer_allows_paths_with_trailing_slash(self):
        """Test that paths with trailing slashes are allowed."""
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/about/'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/products/'))

    def test_sanitizer_handles_query_params(self):
        """Test handling of URLs with query parameters."""
        # HTML with query params should be allowed
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/page.html?id=123'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/search?q=test'))

        # Assets with query params should still be blocked
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/style.css?v=2'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/app.js?v=1.2'))

    def test_sanitizer_handles_fragments(self):
        """Test handling of URLs with fragments."""
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/page.html#section'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/about#team'))

    def test_sanitizer_case_insensitive(self):
        """Test that extension matching is case-insensitive."""
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/image.PNG'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/style.CSS'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/app.JS'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/page.HTML'))

    def test_sanitizer_handles_nested_paths(self):
        """Test handling of deeply nested paths."""
        # HTML in nested path
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/blog/2023/12/post.html'))

        # Assets in nested path should be blocked
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/assets/css/main.css'))
        self.assertFalse(UrlPruneStage._sanitizer('http://example.com/images/products/item.png'))

    def test_sanitizer_handles_dots_in_path(self):
        """Test handling of dots in directory names."""
        # Dots in directory names, but no file extension
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/v1.0/about'))
        self.assertTrue(UrlPruneStage._sanitizer('http://example.com/api.v2/docs'))

    def test_sanitizer_handles_malformed_urls(self):
        """Test that malformed URLs are handled gracefully (permissive)."""
        # Should not crash, should be permissive
        self.assertTrue(UrlPruneStage._sanitizer('not-a-valid-url'))
        self.assertTrue(UrlPruneStage._sanitizer(''))

    # ========================================================================
    # Execute Tests
    # ========================================================================

    async def test_execute_empty_lists(self):
        """Test execute with empty URL lists."""
        old_urls = []
        new_urls = []

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        self.assertEqual(result_old, [])
        self.assertEqual(result_new, [])

    async def test_execute_filters_assets_from_old_site(self):
        """Test that assets are filtered from old site URLs."""
        old_urls = [
            'http://old.com/index.html',
            'http://old.com/about.html',
            'http://old.com/assets/styles.css',
            'http://old.com/assets/app.js',
            'http://old.com/images/logo.png'
        ]
        new_urls = ['http://new.com/index.html']

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        # Only HTML files should remain
        self.assertEqual(len(result_old), 2)
        self.assertIn('http://old.com/index.html', result_old)
        self.assertIn('http://old.com/about.html', result_old)
        self.assertNotIn('http://old.com/assets/styles.css', result_old)
        self.assertNotIn('http://old.com/assets/app.js', result_old)
        self.assertNotIn('http://old.com/images/logo.png', result_old)

    async def test_execute_filters_assets_from_new_site(self):
        """Test that assets are filtered from new site URLs."""
        old_urls = ['http://old.com/index.html']
        new_urls = [
            'http://new.com/index.html',
            'http://new.com/about.html',
            'http://new.com/styles.css',
            'http://new.com/script.js',
            'http://new.com/favicon.ico'
        ]

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        # Only HTML files should remain
        self.assertEqual(len(result_new), 2)
        self.assertIn('http://new.com/index.html', result_new)
        self.assertIn('http://new.com/about.html', result_new)
        self.assertNotIn('http://new.com/styles.css', result_new)
        self.assertNotIn('http://new.com/script.js', result_new)
        self.assertNotIn('http://new.com/favicon.ico', result_new)

    async def test_execute_filters_both_sites(self):
        """Test that assets are filtered from both old and new sites."""
        old_urls = [
            'http://old.com/index.html',
            'http://old.com/main.css',
            'http://old.com/blog/post.html'
        ]
        new_urls = [
            'http://new.com/home.html',
            'http://new.com/app.js',
            'http://new.com/blog/article.html'
        ]

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        # Check old URLs
        self.assertEqual(len(result_old), 2)
        self.assertIn('http://old.com/index.html', result_old)
        self.assertIn('http://old.com/blog/post.html', result_old)

        # Check new URLs
        self.assertEqual(len(result_new), 2)
        self.assertIn('http://new.com/home.html', result_new)
        self.assertIn('http://new.com/blog/article.html', result_new)

    async def test_execute_preserves_order(self):
        """Test that URL order is preserved after filtering."""
        old_urls = [
            'http://old.com/a.html',
            'http://old.com/b.css',
            'http://old.com/c.html',
            'http://old.com/d.js',
            'http://old.com/e.html'
        ]
        new_urls = []

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        # Check that order is preserved
        expected = [
            'http://old.com/a.html',
            'http://old.com/c.html',
            'http://old.com/e.html'
        ]
        self.assertEqual(result_old, expected)

    async def test_execute_with_real_mock_site_pattern(self):
        """Test with URL patterns matching mock sites structure."""
        old_urls = [
            'http://localhost:8000/index.html',
            'http://localhost:8000/about.html',
            'http://localhost:8000/services/consulting.html',
            'http://localhost:8000/assets/styles.css',
            'http://localhost:8000/assets/main.js',
            'http://localhost:8000/assets/logo-old.png'
        ]
        new_urls = [
            'http://localhost:8001/index.html',
            'http://localhost:8001/about-us.html',
            'http://localhost:8001/solutions/consulting.html',
            'http://localhost:8001/assets/styles.css',
            'http://localhost:8001/assets/app.js',
            'http://localhost:8001/assets/logo-new.png'
        ]

        result_old, result_new = await self.stage.execute((old_urls, new_urls))

        # Should filter out 3 assets from each site
        self.assertEqual(len(result_old), 3)  # Only HTML files
        self.assertEqual(len(result_new), 3)  # Only HTML files

        # Verify HTML files are kept
        self.assertIn('http://localhost:8000/index.html', result_old)
        self.assertIn('http://localhost:8000/services/consulting.html', result_old)

        # Verify assets are filtered
        self.assertNotIn('http://localhost:8000/assets/styles.css', result_old)
        self.assertNotIn('http://localhost:8001/assets/app.js', result_new)


# ============================================================================
# Test Runner Helper
# ============================================================================

def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestUrlPruneStage):
    attr = getattr(TestUrlPruneStage, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestUrlPruneStage, attr_name, async_test(attr))


if __name__ == '__main__':
    unittest.main()
