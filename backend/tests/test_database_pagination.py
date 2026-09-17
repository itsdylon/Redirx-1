"""Offline regression coverage for capped PostgREST responses."""
from types import SimpleNamespace
import unittest
from uuid import UUID

from src.redirx.database import URLMappingDB, WebPageEmbeddingDB


SESSION = UUID('00000000-0000-0000-0000-000000000001')


class CappedClient:
    def __init__(self, rows, cap=137, fail_on=None):
        self.rows, self.cap, self.fail_on = rows, cap, fail_on
        self.calls = []

    def table(self, name):
        client = self

        class Query:
            def __init__(self):
                self.filters = {}
                self.order_by = None
                self.start, self.end = 0, client.cap - 1

            def select(self, _):
                return self

            def eq(self, field, value):
                self.filters[field] = value
                return self

            def order(self, field):
                self.order_by = field
                return self

            def range(self, start, end):
                self.start, self.end = start, end
                return self

            def execute(self):
                client.calls.append((name, dict(self.filters), self.start))
                if len(client.calls) == client.fail_on:
                    raise RuntimeError('database unavailable')
                rows = [r.copy() for r in client.rows if all(r.get(k) == v for k, v in self.filters.items())]
                if self.order_by:
                    rows.sort(key=lambda r: r[self.order_by])
                count = min(client.cap, self.end - self.start + 1)
                return SimpleNamespace(data=rows[self.start:self.start + count])

        return Query()


class TestDatabasePagination(unittest.TestCase):
    def rows(self, count):
        return [{'id': f'{i:08d}', 'session_id': str(SESSION), 'needs_review': i % 2 == 0,
                 'site_type': 'old', 'embedding': '[0.1, 0.2]'} for i in reversed(range(count))]

    def test_largest_self_serve_boundary_and_next_row_are_not_truncated(self):
        client = CappedClient(self.rows(15001))
        rows = URLMappingDB(client).get_mappings_by_session(SESSION)
        self.assertEqual(len(rows), 15001)
        self.assertEqual(len({r['id'] for r in rows}), 15001)
        self.assertEqual(rows[0]['id'], '00000000')
        self.assertEqual(rows[-1]['id'], '00015000')

    def test_filters_apply_to_every_page_including_false(self):
        data = self.rows(1000) + [{**r, 'session_id': 'other'} for r in self.rows(1000)]
        client = CappedClient(data)
        rows = URLMappingDB(client).get_mappings_by_session(SESSION, needs_review=False)
        self.assertEqual(len(rows), 500)
        self.assertTrue(all(not r['needs_review'] and r['session_id'] == str(SESSION) for r in rows))
        self.assertTrue(all(c[1] == {'session_id': str(SESSION), 'needs_review': False} for c in client.calls))

    def test_embedding_vectors_are_parsed_on_every_page(self):
        client = CappedClient(self.rows(1200) + [{**r, 'site_type': 'new'} for r in self.rows(50)])
        rows = WebPageEmbeddingDB(client).get_embeddings_by_session(SESSION, 'old')
        self.assertEqual(len(rows), 1200)
        self.assertTrue(all(r['embedding'] == [0.1, 0.2] for r in rows))

    def test_empty_and_exact_page_multiple_terminate(self):
        for count in (0, 1000):
            client = CappedClient(self.rows(count), cap=500)
            self.assertEqual(len(URLMappingDB(client).get_mappings_by_session(SESSION)), count)
            self.assertEqual(len(client.calls), count // 500 + 1)

    def test_later_page_failure_does_not_return_partial_results(self):
        client = CappedClient(self.rows(1000), fail_on=2)
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            URLMappingDB(client).get_mappings_by_session(SESSION)


if __name__ == '__main__':
    unittest.main()
