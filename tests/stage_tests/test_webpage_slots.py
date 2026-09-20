"""WebPage must stay dict-free: a full job holds 35,000 of these at once."""
import copy
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from src.redirx.stages import WebPage


HTML = '<html><head><title>Example</title></head><body><h1>Example</h1><p>Body copy.</p></body></html>'


class WebPageSlotsTests(unittest.TestCase):
    def test_instances_carry_no_dict(self):
        page = WebPage('https://old.example.com/a', HTML)
        self.assertFalse(hasattr(page, '__dict__'))

    def test_every_constructor_attribute_has_a_slot(self):
        page = WebPage('https://old.example.com/a', HTML)
        declared = {
            name if not name.startswith('__') else f'_WebPage{name}'
            for name in WebPage.__slots__
        }
        for name in declared:
            self.assertTrue(hasattr(page, name), name)
        # The private cache is mangled into a real slot, not silently dropped.
        self.assertIn('_WebPage__html_cache', declared)

    def test_unknown_attributes_are_rejected(self):
        page = WebPage('https://old.example.com/a', HTML)
        with self.assertRaises(AttributeError):
            page.unexpected_attribute = 'a new per-instance dict'

    def test_with_url_copies_every_slot(self):
        page = WebPage('https://old.example.com/a', HTML).compact()
        clone = page.with_url('https://old.example.com/a?utm=1')
        self.assertEqual(clone.url, 'https://old.example.com/a?utm=1')
        self.assertEqual(page.url, 'https://old.example.com/a')
        for name in ('html', '_extracted_text', '_title', '_compacted',
                     '_original_html_length', '_content_digest', '_content_store',
                     '_text_reference', 'content_error', '_WebPage__html_cache'):
            self.assertEqual(getattr(clone, name), getattr(page, name), name)
        self.assertFalse(hasattr(clone, '__dict__'))

    def test_copy_module_round_trip_preserves_state(self):
        page = WebPage('https://old.example.com/a', HTML)
        page.content_error = 'content_unavailable'
        clone = copy.copy(page)
        self.assertEqual(clone.content_error, 'content_unavailable')
        self.assertEqual(clone.html, HTML)
        self.assertEqual(hash(clone), hash(page))
        self.assertEqual(clone, page)

    def test_compact_still_releases_html_and_keeps_identity(self):
        page = WebPage('https://old.example.com/a', HTML)
        digest_source = len(HTML)
        page.compact()
        self.assertEqual(page.html, '')
        self.assertEqual(page.html_length, digest_source)
        self.assertEqual(page._title, 'Example')
        self.assertIsNotNone(page._content_digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
