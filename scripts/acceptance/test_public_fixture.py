"""Offline fixture admission, byte budgets, and actual production extraction."""
import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree

from scripts.acceptance.public_fixture import (ROOT, SITEMAP_MAX_BYTES, SITEMAP_MAX_URLS, build_plan,
    generate, page_path, public_origin, render_page, semantic_text, sitemap_documents)
from backend.services.discovery_parsing import sitemap_urls
from src.redirx.stages import ExactUrlMatchStage, HtmlPruneStage, UrlPruneStage, WebPage

OLD = 'https://old-fixture.example.com'
NEW = 'https://new-fixture.example.com'


class PublicFixtureTest(unittest.TestCase):
    def test_500_and_501_boundaries_count_homepage_and_fit_real_import_policy(self):
        for count in (500, 501):
            plan = build_plan(count, 600, OLD, NEW)
            self.assertEqual(len(plan['manifest']['mappings']), count)
            self.assertEqual(plan['manifest']['mappings'][0]['old_url'], OLD + '/')
            self.assertEqual(plan['manifest']['mappings'][0]['new_url'], NEW + '/')
            for side, expected in (('old', count), ('new', 600)):
                rows = plan['payloads'][side]['rows']
                self.assertEqual(len(set(rows)), expected)
                self.assertEqual(sum(urlsplit(url).path == '/' for url in rows), 1)
                self.assertTrue(all(len(url) == 128 for url in rows[1:]))
                self.assertEqual(plan['admission'][side]['pages'], expected)
                self.assertLess(plan['admission'][side]['request_body_bytes'], 25 * 1024 * 1024)
                self.assertLess(plan['admission'][side]['policy_json_bytes_estimate'] * 1.1, 32 * 1024 * 1024)

    def test_full_capacity_plan_bounds_calls_text_and_vector_payload(self):
        plan = build_plan(15000, 20000, OLD, NEW)
        self.assertEqual(plan['manifest']['unmatched_new_pages'], 5000)
        estimates = plan['estimates']
        self.assertLessEqual(estimates['maximum_extracted_text_bytes'], 1024)
        for scenario, calls in (('one_job', 35000), ('two_jobs', 70000), ('one_plus_two_jobs', 105000)):
            row = estimates['scenarios'][scenario]
            self.assertEqual(row['embedding_calls_without_retries'], calls)
            self.assertEqual(row['http_attempts_with_five_worker_attempts_upper'], calls * 45)
            self.assertEqual(row['raw_float32_vector_bytes'], calls * 1536 * 4)
            self.assertLessEqual(row['persisted_text_bytes'], calls * 1024)
        self.assertEqual(estimates['html_bytes_total'], 35000 * 65536)
        self.assertEqual(len({semantic_text(index) for index in range(20000)}), 20000)

    def test_full_capacity_sitemaps_are_bounded_and_actual_parser_recovers_exact_public_urls(self):
        # Exercise both limits without producing gigabytes of padded HTML.
        long_origin = 'https://' + '.'.join(['a' * 63, 'b' * 63, 'c' * 63, 'd' * 50, 'com'])
        for origin, count in ((OLD, 15000), (NEW, 20000), (long_origin, 20000)):
            with self.subTest(origin=origin, count=count):
                urls = [origin + page_path('new', index, origin) for index in range(count)]
                documents = sitemap_documents(origin, urls)
                index = list(sitemap_urls(documents['sitemap.xml'].decode()))
                self.assertEqual(index[0], 'sitemapindex')
                restored = []
                counts = []
                for loc in index[1:]:
                    self.assertEqual(urlsplit(loc).scheme + '://' + urlsplit(loc).netloc, origin)
                    filename = urlsplit(loc).path.removeprefix('/')
                    self.assertIn(filename, documents)
                    parsed = list(sitemap_urls(documents[filename].decode()))
                    self.assertEqual(parsed[0], 'urlset')
                    counts.append(len(parsed) - 1)
                    self.assertLessEqual(counts[-1], SITEMAP_MAX_URLS)
                    restored.extend(parsed[1:])
                self.assertEqual(restored, urls)
                self.assertEqual(len(set(restored)), count)
                self.assertEqual(sum(urlsplit(url).path == '/' for url in restored), 1)
                self.assertTrue(all(len(raw) <= SITEMAP_MAX_BYTES < 2 * 1024 * 1024 for raw in documents.values()))
                self.assertEqual(len(documents), len(index))
                if origin == long_origin:
                    self.assertLess(counts[0], SITEMAP_MAX_URLS)  # byte budget, not just count, forced the split

    def test_small_sitemap_remains_one_urlset(self):
        urls = [OLD + page_path('old', index, OLD) for index in range(600)]
        documents = sitemap_documents(OLD, urls)
        self.assertEqual(set(documents), {'sitemap.xml'})
        self.assertEqual(list(sitemap_urls(documents['sitemap.xml'].decode())), ['urlset', *urls])

    def test_actual_webpage_extraction_and_both_pruning_stages_leave_embedding_work(self):
        plan = build_plan(8, 10, OLD, NEW)
        old_urls, new_urls = (plan['payloads'][side]['rows'] for side in ('old', 'new'))
        async def check():
            self.assertEqual(await UrlPruneStage().execute((old_urls, new_urls)), (old_urls, new_urls))
            self.assertEqual(await ExactUrlMatchStage(preserve_url_identity=True).execute((old_urls, new_urls)),
                             (old_urls, new_urls))
            pages = {}
            for side, urls in (('old', old_urls), ('new', new_urls)):
                pages[side] = []
                for index, url in enumerate(urls):
                    raw = render_page(side, index, 65536)
                    self.assertEqual(len(raw), 65536)
                    page = WebPage(url, raw.decode())
                    self.assertEqual(page.extract_text(), semantic_text(index))
                    page.compact()
                    self.assertEqual(page.extract_text(), semantic_text(index))
                    self.assertEqual(page.html_length, 65536)
                    self.assertEqual(page.html, '')
                    pages[side].append(page)
            for index, old in enumerate(pages['old']):
                new = pages['new'][index]
                self.assertNotEqual(old, new)
                self.assertEqual(old.extract_text(), new.extract_text())
                self.assertEqual(hashlib.sha256(old.extract_text().encode()).hexdigest(),
                                 plan['manifest']['mappings'][index]['semantic_sha256'])
            result = await HtmlPruneStage(preserve_url_identity=True).execute((pages['old'], pages['new']))
            self.assertEqual((len(result[0]), len(result[1]), len(result[2])), (8, 10, 0))
            actual_bytes = sum(len(page.extract_text().encode()) for side in pages.values() for page in side)
            self.assertEqual(actual_bytes, plan['estimates']['scenarios']['one_job']['persisted_text_bytes'])
        with redirect_stdout(io.StringIO()):
            asyncio.run(check())

    def test_emitted_docroots_sitemaps_imports_and_private_metadata(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'fixture'
            plan = generate(output, 500, 600, OLD, NEW, html_bytes=2048)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual({entry.name for entry in output.iterdir()},
                             {'old', 'new', 'import-old.json', 'import-new.json', 'expected-mappings.json',
                              'import-admission.json', 'estimates.json'})
            for side, count in (('old', 500), ('new', 600)):
                root = output / side
                self.assertEqual(len(list(root.iterdir())), count + 2)
                self.assertTrue((root / 'index.html').is_file())
                urls = [loc.text for loc in ElementTree.parse(root / 'sitemap.xml').findall(
                    './/{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                self.assertEqual(urls, plan['payloads'][side]['rows'])
                self.assertIn('Sitemap: ' + plan['manifest']['origins'][side] + '/sitemap.xml',
                              (root / 'robots.txt').read_text())
                self.assertEqual(json.loads((output / f'import-{side}.json').read_text()), plan['payloads'][side])
                self.assertTrue(all((root / ('index.html' if urlsplit(url).path == '/' else urlsplit(url).path[1:])).stat().st_size == 2048
                                    for url in urls))
            for entry in output.iterdir():
                if entry.is_file():
                    self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o600)
            before = (output / 'expected-mappings.json').read_bytes()
            with self.assertRaises(FileExistsError):generate(output, 501, 600, OLD, NEW)
            self.assertEqual((output / 'expected-mappings.json').read_bytes(), before)

    def test_invalid_origins_counts_and_dangling_output_do_not_write(self):
        for origin in ('http://example.com', 'https://localhost', 'https://127.0.0.1', 'https://10.0.0.1',
                       'https://[::1]', 'https://x.local', 'https://user:password@example.com',
                       'https://example.com/path', 'https://example.com?secret=value', 'https://example.com:8443'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):public_origin(origin)
        for old, new in ((0, 600), (15001, 20000), (15000, 20001), (501, 500), (True, 600)):
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):build_plan(old, new, OLD, NEW)
        with self.assertRaises(ValueError):build_plan(1, 1, OLD, OLD + '/')
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'dangling'
            output.symlink_to(Path(parent) / 'absent')
            with self.assertRaises(FileExistsError):generate(output, 1, 1, OLD, NEW)
            self.assertTrue(output.is_symlink())
            self.assertFalse((Path(parent) / 'absent').exists())

    def test_cli_runs_outside_checkout_and_refuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            command = [sys.executable, str(ROOT / 'scripts/acceptance/public_fixture.py'),
                       '--old-pages', '1', '--new-pages', '2', '--old-origin', OLD,
                       '--new-origin', NEW, '--output', str(Path(parent) / 'new-output')]
            result = subprocess.run(command, cwd=parent, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['generated'])
            result = subprocess.run(command, cwd=parent, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)['generated'])


if __name__ == '__main__':
    unittest.main()
