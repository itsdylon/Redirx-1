import unittest
import os
import sys

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import WebPage


class TestTextExtraction(unittest.TestCase):
    """Unit tests for WebPage text extraction methods."""

    def test_extract_text_removes_scripts(self):
        """Test that script tags are removed from extracted text."""
        html = '''
        <html>
            <head><title>Test</title></head>
            <body>
                <script>alert('Should not appear');</script>
                <p>This is content.</p>
            </body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        text = page.extract_text()

        self.assertNotIn('alert', text)
        self.assertNotIn('Should not appear', text)
        self.assertIn('This is content', text)

    def test_extract_text_removes_styles(self):
        """Test that style tags are removed from extracted text."""
        html = '''
        <html>
            <head>
                <style>body { color: red; }</style>
            </head>
            <body>
                <p>Visible content</p>
            </body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        text = page.extract_text()

        self.assertNotIn('color: red', text)
        self.assertIn('Visible content', text)

    def test_extract_text_removes_nav_elements(self):
        """Test that navigation elements are removed."""
        html = '''
        <html>
            <body>
                <nav>Navigation Menu</nav>
                <header>Site Header</header>
                <main>Main Content</main>
                <footer>Footer Text</footer>
            </body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        text = page.extract_text()

        self.assertNotIn('Navigation Menu', text)
        self.assertNotIn('Site Header', text)
        self.assertNotIn('Footer Text', text)
        self.assertIn('Main Content', text)

    def test_extract_text_normalizes_whitespace(self):
        """Test that excessive whitespace is normalized."""
        html = '''
        <html>
            <body>
                <p>Multiple     spaces    here</p>
                <p>
                    Newlines
                    everywhere
                </p>
            </body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        text = page.extract_text()

        # Should have single spaces between words
        self.assertNotIn('     ', text)
        self.assertIn('Multiple spaces here', text)
        self.assertIn('Newlines everywhere', text)

    def test_extract_text_prioritizes_main_content(self):
        """Test that main/article tags are prioritized."""
        html = '''
        <html>
            <body>
                <aside>Sidebar content</aside>
                <main>
                    <article>Article content here</article>
                </main>
            </body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        text = page.extract_text()

        self.assertIn('Article content here', text)

    def test_extract_text_caching(self):
        """Test that extracted text is cached."""
        html = '<html><body><p>Content</p></body></html>'
        page = WebPage('http://test.com', html)

        # First extraction
        text1 = page.extract_text()
        # Second extraction should return cached value
        text2 = page.extract_text()

        self.assertEqual(text1, text2)
        self.assertIs(text1, text2)  # Same object reference

    def test_extract_text_truncates_long_content(self):
        """Test that very long content is truncated."""
        # Create HTML with > 32k characters
        long_text = 'A' * 40000
        html = f'<html><body><p>{long_text}</p></body></html>'
        page = WebPage('http://test.com', html)

        text = page.extract_text()

        # Should be truncated to ~32k chars
        self.assertLessEqual(len(text), 32000)

    def test_extract_text_fallback_to_url(self):
        """Test that URL is used as fallback for empty content."""
        html = '<html><body><script>only script</script></body></html>'
        url = 'http://example.com/page'
        page = WebPage(url, html)

        text = page.extract_text()

        # Should fall back to URL when text is too short
        self.assertEqual(text, url)

    def test_extract_text_handles_invalid_html(self):
        """Test that invalid HTML is handled gracefully."""
        html = 'Not valid HTML at all'
        url = 'http://test.com'
        page = WebPage(url, html)

        # Should not raise exception
        text = page.extract_text()
        self.assertIsInstance(text, str)

    def test_extract_title_from_title_tag(self):
        """Test extracting title from <title> tag."""
        html = '''
        <html>
            <head><title>Page Title</title></head>
            <body><h1>Header</h1></body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        title = page.extract_title()

        self.assertEqual(title, 'Page Title')

    def test_extract_title_fallback_to_h1(self):
        """Test fallback to h1 when no title tag."""
        html = '''
        <html>
            <body><h1>Main Header</h1></body>
        </html>
        '''
        page = WebPage('http://test.com', html)
        title = page.extract_title()

        self.assertEqual(title, 'Main Header')

    def test_extract_title_returns_empty_string(self):
        """Test that empty string is returned when no title found."""
        html = '<html><body><p>No title here</p></body></html>'
        page = WebPage('http://test.com', html)
        title = page.extract_title()

        self.assertEqual(title, '')

    def test_extract_title_caching(self):
        """Test that title extraction is cached."""
        html = '<html><head><title>Cached Title</title></head></html>'
        page = WebPage('http://test.com', html)

        title1 = page.extract_title()
        title2 = page.extract_title()

        self.assertEqual(title1, title2)
        self.assertIs(title1, title2)  # Same object reference

    def test_extract_title_strips_whitespace(self):
        """Test that title whitespace is stripped."""
        html = '''
        <html>
            <head><title>  Spaced Title  </title></head>
        </html>
        '''
        page = WebPage('http://test.com', html)
        title = page.extract_title()

        self.assertEqual(title, 'Spaced Title')

    def test_extract_title_handles_invalid_html(self):
        """Test that invalid HTML is handled gracefully."""
        html = 'Not valid HTML'
        page = WebPage('http://test.com', html)

        # Should not raise exception
        title = page.extract_title()
        self.assertEqual(title, '')


if __name__ == '__main__':
    unittest.main()
