import unittest
import xml.etree.ElementTree as ET
from backend.services.discovery_parsing import crawl_links, sitemap_urls, CHUNK_CHARACTERS


class StreamParsingTests(unittest.TestCase):
    def test_html_links_preserve_entities_case_and_ignore_script_and_nonanchors(self):
        text = ' ' * (CHUNK_CHARACTERS-8) + '<A HREF="/Path?q=1&amp;x=2">x</A><script>"<a href=\"/fake\">"</script><link href="/asset"><a href="/first" href="/last"><a>'
        self.assertEqual(list(crawl_links(text)), ['/Path?q=1&x=2', '/last'])

    def test_xml_namespaces_chunk_boundaries_first_loc_and_malformed_tail(self):
        text = '<urlset xmlns="urn:fixture">' + '<url><loc>https://fixture.example/a?x=1&amp;y=2</loc><loc>ignored</loc></url>' * 5000 + '</urlset>'
        rows = list(sitemap_urls(text))
        self.assertEqual(rows[0], 'urlset')
        self.assertEqual(len(rows), 5001)
        self.assertEqual(set(rows[1:]), {'https://fixture.example/a?x=1&y=2'})
        with self.assertRaises(ET.ParseError):
            list(sitemap_urls(text[:-9]))
        for invalid in ('<other/>', '<urlset><url/></urlset>', '<urlset><url><loc/></url></urlset>'):
            with self.assertRaises(ValueError):
                list(sitemap_urls(invalid))
        self.assertEqual(list(sitemap_urls('<sitemapindex><sitemap><loc>https://fixture.example/map.xml</loc></sitemap></sitemapindex>')), ['sitemapindex', 'https://fixture.example/map.xml'])
