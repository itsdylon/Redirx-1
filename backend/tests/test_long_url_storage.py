"""056 long-URL storage acceptance: the 8192-character contract end to end.

Extends the deepest existing native fixture (001/019/026/032/034-051/053/054/055
plus production-shaped NO ACTION engine FKs) with 056, so every inherited pivot
journey, evidence-fence and parent-cleanup test re-runs under the new index
shapes. 052 is not applied: it introduces no object 056 touches and needs a real
pgvector install, which this fixture does not have.

URLs here are incompressible sha256 hex, not repeated characters: a repeated
character TOASTs away and never reaches the index-key limit that 026/032/040/043
actually hit. Each URL ends in `/<digit>` so the loopback origin fixture keeps
serving it.

This suite makes no claim about matching quality. Engine persistence is driven
through the privileged 050 RPCs, which is exactly the path the URL-membership
guard fences, rather than through the matcher.
"""
import asyncio
import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import psycopg

from backend.services.inventory_import_service import (
    InventoryImportService, InvalidInputError, RepositoryUnavailableError)
from backend.services.inventory_policy import preflight_inventory
from backend.services.migration_monitoring_service import MigrationMonitoringService, run_monitoring_batch
from backend.services.migration_verification_service import MigrationVerificationService, run_verification_batch
from backend.tests import test_pivot_parent_cleanup as cleanup
from backend.tests.test_pivot_product_journey import ROOT, A
from backend.tests.helpers.pivot_native_client import json_value

URL_LENGTH = 8020
CONTRACT_LENGTH = 8192


def entropy(seed, size):
    """Incompressible hex of exactly `size` characters."""
    out = ''
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f'{seed}:{counter}'.encode()).hexdigest()
        counter += 1
    return out[:size]


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class LongURLStorage(cleanup.PivotParentCleanup):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with psycopg.connect(cls.dsn, autocommit=True) as c:
            c.execute((ROOT / 'database/migrations/056_exact_long_url_storage.sql').read_text())

    # ---- helpers -------------------------------------------------------

    def long_url(self, side, index, seed='j', length=URL_LENGTH):
        """A valid absolute URL of exactly `length` characters ending in /<index>."""
        origin = self.origins[side]
        tail = f'/{index}'
        prefix = origin + '/page/'
        size = length - len(prefix) - len(tail)
        self.assertGreater(size, 64, 'fixture origin is too long for this URL length')
        url = prefix + entropy(f'{seed}:{side}:{index}', size) + tail
        self.assertEqual(len(url), length)
        return url

    def variant(self, url):
        """A different URL of the same length sharing every character but one."""
        index = url.rindex('/')
        swapped = 'e' if url[index - 1] != 'e' else 'f'
        changed = url[:index - 1] + swapped + url[index:]
        self.assertEqual(len(changed), len(url))
        self.assertNotEqual(changed, url)
        return changed

    def plan_import_long(self, count, seed='j', length=URL_LENGTH):
        key = uuid4().hex
        planned = self.tool('plan_migration', {'old_site': self.origins['old'],
                            'new_site': self.origins['new'], 'idempotency_key': key})
        mid = planned['migration_id']
        inventories, urls = {}, {}
        for side in ('old', 'new'):
            urls[side] = [self.long_url(side, i, seed, length) for i in range(count)]
            response = self.request('POST', f'/migrations/{mid}/inventories',
                {'side': side, 'rows': [{'url': u, 'provenance': ['csv']} for u in urls[side]],
                 'idempotency_key': f'{side}-{key}'})
            self.assertEqual(response['status'], 'succeeded', response)
            inventories[side] = response['data']['inventory']['id']
        return mid, inventories, urls

    def claim_and_authorize(self, worker):
        job = json_value(self.sql("SELECT * FROM claim_next_job(%s,now()+interval '10 minutes')", [worker])[0])
        self.runs.authorize_dispatch(job, worker)
        return job

    def constraint_name(self, table, kind='x'):
        return [r['conname'] for r in self.sql(
            'SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype=%s', [table, kind])]

    # ---- storage shape -------------------------------------------------

    def test_056_replaced_every_wide_url_key_and_left_the_cursor_index(self):
        indexes = {r['indexname']: r['indexdef'] for r in self.sql(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename IN "
            "('session_discovered_urls','migration_verification_items','gsc_migration_metrics')")}
        for gone in ('session_discovered_urls_session_id_side_url_key',
                     'idx_session_discovered_urls_inventory_url',
                     'idx_session_discovered_urls_inventory_count_key',
                     'migration_verification_items_verification_id_source_url_key',
                     'gsc_migration_metrics_pkey'):
            self.assertNotIn(gone, indexes, f'{gone} still carries a full URL in a B-tree key')
        for hashed in ('session_discovered_urls_session_side_url_exact',
                       'session_discovered_urls_inventory_url_exact',
                       'migration_verification_items_source_url_exact',
                       'gsc_migration_metrics_url_exact'):
            self.assertIn('USING hash', indexes[hashed], hashed)
        for digest in ('idx_session_discovered_urls_inventory_url_hash',
                       'idx_session_discovered_urls_inventory_count_key_hash',
                       'idx_gsc_migration_metrics_url_hash'):
            self.assertIn('md5', indexes[digest], digest)
        # 032's paging cursor and 043's claim index are untouched.
        self.assertIn('idx_session_discovered_urls_inventory_cursor', indexes)
        self.assertIn('migration_verification_items_claim', indexes)
        # Uniqueness moved to exclusion constraints, so no URL key remains unique.
        self.assertEqual(self.constraint_name('session_discovered_urls', 'u'), [])
        self.assertEqual(sorted(self.constraint_name('session_discovered_urls', 'x')),
                         ['session_discovered_urls_inventory_url_exact',
                          'session_discovered_urls_session_side_url_exact'])

    def test_legacy_btree_shapes_reject_the_same_url_the_corrected_shapes_store(self):
        """Reproduce the release blocker against the real pre-056 key definitions."""
        url = self.long_url('old', 0, seed='repro')
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute('CREATE TEMP TABLE legacy_shape(session_id uuid, side text NOT NULL, url text NOT NULL,'
                      ' inventory_id uuid, UNIQUE(session_id,side,url))')
            c.execute('CREATE UNIQUE INDEX legacy_inventory_url ON legacy_shape(inventory_id,url)'
                      ' WHERE inventory_id IS NOT NULL')
            # 026 indexes inventory-only rows too, so a NULL session does not dodge it.
            for args in ([None, 'old', url, uuid4()], [uuid4(), 'old', url, None]):
                with self.assertRaises(psycopg.errors.ProgramLimitExceeded) as raised:
                    c.execute('INSERT INTO legacy_shape(session_id,side,url,inventory_id) VALUES(%s,%s,%s,%s)', args)
                self.assertEqual(raised.exception.sqlstate, '54000')
                self.assertIn('exceeds btree version 4 maximum 2704', str(raised.exception))
            c.execute('DROP TABLE legacy_shape')
            c.execute('CREATE TEMP TABLE corrected_shape(session_id uuid, side text NOT NULL, url text NOT NULL,'
                      " CONSTRAINT corrected_exact EXCLUDE USING hash"
                      " (((session_id::text || ':' || side || ':' || url) COLLATE \"C\") WITH =)"
                      " WHERE (session_id IS NOT NULL))")
            sid = uuid4()
            c.execute('INSERT INTO corrected_shape VALUES(%s,%s,%s)', [sid, 'old', url])
            self.assertEqual(c.execute('SELECT url FROM corrected_shape').fetchone()[0], url)

    def test_gsc_metrics_keeps_not_null_and_gains_a_replica_identity(self):
        """040 deletes every row per sync; without a replica identity that fails under logical replication."""
        columns = {r['column_name']: r['is_nullable'] for r in self.sql(
            "SELECT column_name,is_nullable FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='gsc_migration_metrics'")}
        self.assertEqual(columns['migration_id'], 'NO')
        self.assertEqual(columns['url'], 'NO')
        self.assertEqual(self.sql("SELECT relreplident FROM pg_class WHERE oid='gsc_migration_metrics'::regclass")
                         [0]['relreplident'], 'f')
        mid = self.tool('plan_migration', {'old_site': self.origins['old'], 'new_site': self.origins['new'],
                                           'idempotency_key': uuid4().hex})['migration_id']
        url = self.long_url('old', 1, seed='replica')
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)"
                 " VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')", [mid, A, self.origins['old'] + '/'])
        self.sql('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,7,9)', [mid, url])
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute('CREATE PUBLICATION long_url_probe FOR TABLE gsc_migration_metrics')
            try:
                c.execute('UPDATE gsc_migration_metrics SET clicks=8 WHERE migration_id=%s', [mid])
                c.execute('DELETE FROM gsc_migration_metrics WHERE migration_id=%s', [mid])
            finally:
                c.execute('DROP PUBLICATION long_url_probe')

    def test_function_privileges_keep_053_decisions(self):
        expected = {
            'migration_engine_url_belongs(uuid,text,text)': {'anon': False, 'authenticated': False, 'service_role': False},
            'monitor_observed_clicks(uuid,text)': {'anon': False, 'authenticated': False, 'service_role': True},
            'checkpoint_inventory_discovery(uuid,uuid,uuid,uuid,integer,jsonb,jsonb,text)':
                {'anon': False, 'authenticated': False, 'service_role': True},
            'publish_inventory_import(uuid,uuid,text,jsonb)':
                {'anon': False, 'authenticated': False, 'service_role': True},
        }
        for signature, roles in expected.items():
            for role, allowed in roles.items():
                granted = self.sql('SELECT has_function_privilege(%s,%s,%s) AS ok', [role, signature, 'EXECUTE'])[0]['ok']
                self.assertEqual(granted, allowed, f'{role} EXECUTE on {signature}')
        self.assertEqual(self.sql("SELECT count(*) AS n FROM pg_proc WHERE pronamespace='public'::regnamespace"
                                  " AND proname IN ('migration_engine_url_belongs','monitor_observed_clicks',"
                                  "'checkpoint_inventory_discovery','publish_inventory_import')")[0]['n'], 4,
                         '056 created an overload instead of replacing a function')

    # ---- import and identity ------------------------------------------

    def test_import_stores_long_urls_byte_exact_and_keeps_variants_distinct(self):
        url = self.long_url('old', 0, seed='import')
        sibling = self.variant(url)          # differs inside an 8000-character shared prefix
        self.assertEqual(sibling[:4000], url[:4000])
        canonical = self.long_url('old', 0, seed='import-canonical')
        mid = self.tool('plan_migration', {'old_site': self.origins['old'], 'new_site': self.origins['new'],
                                           'idempotency_key': uuid4().hex})['migration_id']
        response = self.request('POST', f'/migrations/{mid}/inventories',
            {'side': 'old', 'rows': [{'url': url, 'provenance': ['csv']}, {'url': sibling, 'provenance': ['csv']},
                                     {'url': canonical, 'provenance': ['sitemap']}],
             'idempotency_key': uuid4().hex})
        self.assertEqual(response['status'], 'succeeded', response)
        inventory = response['data']['inventory']['id']
        stored = [r['url'] for r in self.sql(
            'SELECT url FROM session_discovered_urls WHERE inventory_id=%s ORDER BY url', [inventory])]
        self.assertEqual(sorted(stored), sorted([url, sibling, canonical]))
        self.assertTrue(all(len(value) == URL_LENGTH for value in stored))
        self.assertEqual(response['data']['inventory']['page_count'], 3)

    def test_duplicate_long_url_import_still_answers_invalid_input_not_unavailable(self):
        """034's guard catches 23505; the exclusion raises 23P01, so it must name both.

        The application policy deduplicates identical rows before the RPC, so this
        drives the boundary the guard actually defends: a p_inventory payload whose
        item repeats an original_url. Without exclusion_violation in 034's handler
        the raw error escapes the P0001 mapping and the caller is told the import is
        temporarily unavailable, which is retryable, instead of invalid.
        """
        url = self.long_url('old', 0, seed='dup')
        mid = self.tool('plan_migration', {'old_site': self.origins['old'], 'new_site': self.origins['new'],
                                           'idempotency_key': uuid4().hex})['migration_id']
        service = InventoryImportService(self.repo)
        policy = preflight_inventory([{'url': url, 'provenance': ['csv']}],
                                     declared_origins=[self.origins['old']], side='old')
        self.assertEqual(len(policy['items']), 1)
        policy['items'][0]['original_urls'] = [url, url]
        with patch('backend.services.inventory_import_service.preflight_inventory', return_value=policy):
            with self.assertRaises(InvalidInputError) as raised:
                service.import_inventory(A, mid, 'old', [{'url': url, 'provenance': ['csv']}], uuid4().hex)
        self.assertNotIsInstance(raised.exception, RepositoryUnavailableError)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM inventory_snapshots WHERE migration_id=%s"
                                  " AND status<>'pending'", [mid])[0]['n'], 0)

    def test_exact_duplicates_raise_exclusion_violations_in_every_rewritten_scope(self):
        mid, inventories, urls = self.plan_import_long(1, seed='exclusion')
        # 032 freezes a published inventory, so duplicate detection is exercised on a
        # pending one, which is the state every writer actually inserts into.
        pending = self.sql("INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)"
                           " VALUES(%s,%s,'old','explicit_inventory_v1') RETURNING id", [mid, A])[0]['id']
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute('INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,%s,%s,%s)',
                      [pending, 'old', urls['old'][0], urls['old'][0]])
            with self.assertRaises(psycopg.errors.ExclusionViolation) as raised:
                c.execute('INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,%s,%s,%s)',
                          [pending, 'old', urls['old'][0], urls['old'][0]])
            self.assertEqual(raised.exception.sqlstate, '23P01')
            self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)"
                     " VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')",
                     [mid, A, self.origins['old'] + '/'])
            c.execute('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,1,1)',
                      [mid, urls['old'][0]])
            with self.assertRaises(psycopg.errors.ExclusionViolation):
                c.execute('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,2,2)',
                          [mid, urls['old'][0]])
            # A different URL sharing the whole indexed prefix is not a duplicate.
            c.execute('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,3,3)',
                      [mid, self.variant(urls['old'][0])])
            self.assertEqual(c.execute('SELECT count(*) FROM gsc_migration_metrics WHERE migration_id=%s',
                                       [mid]).fetchone()[0], 2)

    def test_hash_collisions_and_null_scopes_keep_legacy_semantics(self):
        """A 32-bit index collision must not merge two URLs, and NULL scopes stay unconstrained."""
        colliding = self.sql("""
            WITH s AS (SELECT %s||md5(g::text)||g::text AS u FROM generate_series(1,400000) g)
            SELECT min(u) AS a, max(u) AS b FROM s GROUP BY hashtext(u) HAVING count(*)>1 LIMIT 1
        """, [self.origins['old'] + '/collide/'])
        self.assertTrue(colliding, 'no 32-bit hashtext collision found in the probe space')
        a, b = colliding[0]['a'], colliding[0]['b']
        self.assertNotEqual(a, b)
        mid, inventories, _ = self.plan_import_long(1, seed='collide')
        with psycopg.connect(self.dsn, autocommit=True) as c:
            # The snapshot is published, so mutate a pending one the trigger allows.
            pending = c.execute("INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)"
                                " VALUES(%s,%s,'old','explicit_inventory_v1') RETURNING id", [mid, A]).fetchone()[0]
            for url in (a, b):
                c.execute('INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,%s,%s,%s)',
                          [pending, 'old', url, url])
            self.assertEqual(c.execute('SELECT count(*) FROM session_discovered_urls WHERE inventory_id=%s',
                                       [pending]).fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT hashtext(%s)=hashtext(%s)', [a, b]).fetchone()[0], True)
            # 026's UNIQUE(session_id,side,url) treated NULL sessions as distinct; the
            # partial exclusion must keep that, and inventory scope stays enforced.
            long_url = self.long_url('old', 0, seed='nullscope')
            c.execute("INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,'old',%s,%s)",
                      [pending, long_url, long_url])
            with self.assertRaises(psycopg.errors.ExclusionViolation):
                c.execute("INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,'old',%s,%s)",
                          [pending, long_url, long_url])

    def test_legacy_session_scope_allows_repeated_null_inventory_rows_and_rejects_duplicates(self):
        session = self.sql("INSERT INTO migration_sessions(user_id,status) VALUES(%s,'completed') RETURNING id",
                           [A])[0]['id']
        url = self.long_url('old', 0, seed='legacy-session')
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute("INSERT INTO session_discovered_urls(session_id,side,url) VALUES(%s,'old',%s)", [session, url])
            with self.assertRaises(psycopg.errors.ExclusionViolation):
                c.execute("INSERT INTO session_discovered_urls(session_id,side,url) VALUES(%s,'old',%s)",
                          [session, url])
            # side is part of the key, so the same URL on the other side is allowed.
            c.execute("INSERT INTO session_discovered_urls(session_id,side,url) VALUES(%s,'new',%s)", [session, url])
            # No session and no inventory is rejected by 032's trigger, not by an index.
            with self.assertRaisesRegex(psycopg.Error, 'requires a session or inventory'):
                c.execute("INSERT INTO session_discovered_urls(side,url) VALUES('old',%s)", [url])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM session_discovered_urls WHERE session_id=%s',
                                  [session])[0]['n'], 2)

    # ---- discovery -----------------------------------------------------

    def test_discovery_checkpoint_merges_replayed_long_urls_without_duplicating(self):
        mid = self.tool('plan_migration', {'old_site': self.origins['old'], 'new_site': self.origins['new'],
                                           'idempotency_key': uuid4().hex})['migration_id']
        url = self.long_url('old', 0, seed='discovery')
        request = {'root': self.origins['old'], 'side': 'old', 'origins': [self.origins['old']],
                   'limits': {'max_urls': 1}}
        started = self.native.rpc('start_inventory_discovery', {'p_user_id': A, 'p_migration_id': mid,
            'p_side': 'old', 'p_idempotency_key': uuid4().hex, 'p_request': request,
            'p_state': {}}).execute().data
        operation, inventory = started['operation_id'], started['inventory_id']

        def checkpoint(rows, terminal=None):
            claim = self.native.rpc('claim_inventory_discovery', {'p_user_id': A, 'p_migration_id': mid,
                'p_operation_id': operation}).execute().data
            self.assertTrue(claim['claimed'], claim)
            return self.native.rpc('checkpoint_inventory_discovery', {'p_user_id': A, 'p_migration_id': mid,
                'p_operation_id': operation, 'p_lease_token': claim['lease_token'], 'p_revision': claim['revision'],
                'p_state': {}, 'p_rows': rows, 'p_terminal': terminal}).execute().data

        row = {'url': url, 'count_key': url, 'sources': ['sitemap']}
        self.assertEqual(checkpoint([row])['page_count'], 1)
        # The same URL again merges provenance through the UPDATE path, not a second row.
        self.assertEqual(checkpoint([{**row, 'sources': ['crawl']}])['page_count'], 1)
        stored = self.sql('SELECT url,count_key,sources FROM session_discovered_urls WHERE inventory_id=%s',
                          [inventory])
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['url'], url)
        self.assertEqual(stored[0]['sources'], ['crawl', 'sitemap'])
        # A second distinct count_key exceeds max_urls=1 and must be dropped, not stored.
        second = self.long_url('old', 1, seed='discovery')
        result = checkpoint([{'url': second, 'count_key': second, 'sources': ['sitemap']}])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.sql('SELECT count(*) AS n FROM session_discovered_urls WHERE inventory_id=%s',
                                  [inventory])[0]['n'], 1)
        self.assertIn('capacity_exceeded',
                      self.sql('SELECT coverage FROM inventory_snapshots WHERE id=%s', [inventory])[0]['coverage']['reasons'])

    def test_checkpoint_read_modify_write_is_serialized_by_the_inventory_lock(self):
        """UPDATE-then-INSERT replaces an upsert, so the snapshot lock must make it atomic."""
        mid = self.tool('plan_migration', {'old_site': self.origins['old'], 'new_site': self.origins['new'],
                                           'idempotency_key': uuid4().hex})['migration_id']
        url = self.long_url('old', 0, seed='race')
        inventory = self.sql("INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)"
                             " VALUES(%s,%s,'old','explicit_inventory_v1') RETURNING id", [mid, A])[0]['id']
        first, second = psycopg.connect(self.dsn), psycopg.connect(self.dsn)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        for connection in (first, second):
            connection.execute("SET lock_timeout='4s'")
        first.execute('SELECT 1 FROM inventory_snapshots WHERE id=%s FOR UPDATE', [inventory])
        first.execute("INSERT INTO session_discovered_urls(inventory_id,side,url,count_key) VALUES(%s,'old',%s,%s)",
                      [inventory, url, url])
        with self.assertRaises(psycopg.errors.LockNotAvailable):
            second.execute('SELECT 1 FROM inventory_snapshots WHERE id=%s FOR UPDATE', [inventory])
        second.rollback()
        first.commit()
        second.execute('SELECT 1 FROM inventory_snapshots WHERE id=%s FOR UPDATE', [inventory])
        updated = second.execute("UPDATE session_discovered_urls SET sources=ARRAY['crawl']"
                                 " WHERE inventory_id=%s AND md5(url)=md5(%s) AND url=%s",
                                 [inventory, url, url]).rowcount
        second.commit()
        self.assertEqual(updated, 1, 'the second writer must find the committed row instead of inserting a duplicate')
        self.assertEqual(self.sql('SELECT count(*) AS n FROM session_discovered_urls WHERE inventory_id=%s',
                                  [inventory])[0]['n'], 1)

    # ---- run, metrics, verification, monitoring ------------------------

    def test_engine_membership_accepts_the_stored_long_url_and_rejects_a_near_miss(self):
        mid, inventories, urls = self.plan_import_long(2, seed='engine')
        run = self.tool('run_migration', {'migration_id': mid, 'old_inventory_id': inventories['old'],
            'new_inventory_id': inventories['new'], 'idempotency_key': 'engine-' + mid})
        self.assertEqual(run['status'], 'queued', run)
        rid = run['data']['run_id']
        # 037 copies every inventory URL into the legacy session scope byte for byte.
        copied = self.sql('SELECT side,url,count_key,sources FROM session_discovered_urls WHERE session_id=%s'
                          ' ORDER BY side,url', [run['data']['session_id']])
        original = self.sql('SELECT side,url,count_key,sources FROM session_discovered_urls'
                            ' WHERE inventory_id IN (%s,%s) ORDER BY side,url',
                            [inventories['old'], inventories['new']])
        self.assertEqual(copied, original)
        self.assertEqual(len(copied), 4)
        self.assertTrue(all(len(row['url']) == URL_LENGTH for row in copied))
        job = self.claim_and_authorize('engine-worker')
        self.assertEqual(sorted(job['old_urls']), sorted(urls['old']))
        params = {'p_session_id': job['id'], 'p_run_id': rid, 'p_worker_id': 'engine-worker',
                  'p_attempt_count': job['attempt_count']}
        self.assertTrue(self.sql('SELECT migration_engine_url_belongs(%s,%s,%s) AS ok',
                                 [rid, 'old', urls['old'][0]])[0]['ok'])
        near_miss = self.variant(urls['old'][0])
        self.assertFalse(self.sql('SELECT migration_engine_url_belongs(%s,%s,%s) AS ok',
                                  [rid, 'old', near_miss])[0]['ok'])
        mapping = self.native.rpc('persist_migration_run_mapping', {**params, 'p_old_url': urls['old'][0],
            'p_new_url': urls['new'][0], 'p_confidence_score': 1.0, 'p_match_type': 'exact_html',
            'p_needs_review': False}).execute().data
        self.assertEqual(self.sql('SELECT old_url,new_url FROM url_mappings WHERE id=%s', [mapping['id']])[0],
                         {'old_url': urls['old'][0], 'new_url': urls['new'][0]})
        with self.assertRaisesRegex(Exception, 'invalid_input'):
            self.native.rpc('persist_migration_run_mapping', {**params, 'p_old_url': near_miss,
                'p_new_url': urls['new'][0], 'p_confidence_score': 1.0, 'p_match_type': 'exact_html',
                'p_needs_review': False}).execute()
        self.native.rpc('persist_migration_run_embedding', {**params, 'p_url': urls['old'][0],
            'p_site_type': 'old', 'p_embedding': [1.0] + [0.0] * 1535, 'p_extracted_text': 'fixture body',
            'p_title': 'fixture'}).execute()
        self.assertEqual(self.sql('SELECT url FROM webpage_embeddings WHERE session_id=%s', [job['id']])[0]['url'],
                         urls['old'][0])

    def test_metrics_lookup_matches_full_text_not_the_digest_index(self):
        mid, _, urls = self.plan_import_long(1, seed='metrics')
        sibling = self.variant(urls['old'][0])
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)"
                 " VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')", [mid, A, self.origins['old'] + '/'])
        for url, clicks in ((urls['old'][0], 41), (sibling, 17)):
            self.sql('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,%s,1)',
                     [mid, url, clicks])
        self.assertEqual(self.sql('SELECT monitor_observed_clicks(%s,%s) AS n', [mid, urls['old'][0]])[0]['n'], 41)
        self.assertEqual(self.sql('SELECT monitor_observed_clicks(%s,%s) AS n', [mid, sibling])[0]['n'], 17)
        self.assertIsNone(self.sql('SELECT monitor_observed_clicks(%s,%s) AS n',
                                   [mid, self.variant(self.variant(urls['old'][0])[:-1] + '9')])[0]['n'])

    def test_url_lookups_stay_index_scans_after_the_wide_indexes_are_gone(self):
        mid, inventories, urls = self.plan_import_long(1, seed='plan')
        inventory = inventories['old']
        # Enough rows that a sequential scan would be the cheaper plan if the digest
        # index were unusable, so this asserts the index is actually chosen.
        pending = self.sql("INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)"
                           " VALUES(%s,%s,'old','explicit_inventory_v1') RETURNING id", [mid, A])[0]['id']
        self.sql("INSERT INTO session_discovered_urls(inventory_id,side,url,count_key)"
                 " SELECT %s,'old',%s||n::text,%s||n::text FROM generate_series(1,4000) n",
                 [pending, self.origins['old'] + '/bulk/', self.origins['old'] + '/bulk/'])
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)"
                 " VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')", [mid, A, self.origins['old'] + '/'])
        self.sql("INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions)"
                 " SELECT %s,%s||n::text,0,1 FROM generate_series(1,4000) n", [mid, self.origins['old'] + '/bulk/'])
        self.sql('ANALYZE session_discovered_urls'), self.sql('ANALYZE gsc_migration_metrics')
        plans = {
            'inventory_url': ('SELECT 1 FROM session_discovered_urls WHERE inventory_id=%s'
                              ' AND md5(url)=md5(%s) AND url=%s', [pending, urls['old'][0], urls['old'][0]]),
            'inventory_count_key': ('SELECT 1 FROM session_discovered_urls WHERE inventory_id=%s'
                                    ' AND md5(count_key)=md5(%s) AND count_key=%s',
                                    [pending, urls['old'][0], urls['old'][0]]),
            'metrics_url': ('SELECT clicks FROM gsc_migration_metrics WHERE migration_id=%s'
                            ' AND md5(url)=md5(%s) AND url=%s', [mid, urls['old'][0], urls['old'][0]]),
        }
        for label, (statement, args) in plans.items():
            plan = '\n'.join(r['QUERY PLAN'] for r in self.sql('EXPLAIN ' + statement, args))
            self.assertIn('Index Scan', plan, f'{label} lost its index: {plan}')
            self.assertNotIn('Seq Scan', plan, f'{label} fell back to a sequential scan: {plan}')

    def test_verification_and_monitoring_carry_long_source_urls(self):
        mid, inventories, urls = self.plan_import_long(3, seed='verify')
        run = self.tool('run_migration', {'migration_id': mid, 'old_inventory_id': inventories['old'],
            'new_inventory_id': inventories['new'], 'idempotency_key': 'verify-run-' + mid})
        rid = run['data']['run_id']
        job = self.claim_and_authorize('verify-worker')
        params = {'p_session_id': job['id'], 'p_run_id': rid, 'p_worker_id': 'verify-worker',
                  'p_attempt_count': job['attempt_count']}
        for index in range(3):
            self.native.rpc('persist_migration_run_mapping', {**params, 'p_old_url': urls['old'][index],
                'p_new_url': urls['new'][index], 'p_confidence_score': 1.0, 'p_match_type': 'exact_html',
                'p_needs_review': False}).execute()
        self.runs.finalize_session(job, 'verify-worker', 'completed')
        listed = self.tool('list_matches', {'migration_id': mid, 'run_id': rid, 'limit': 3})
        self.assertEqual(len(listed['data']['items']), 3)
        decided = self.tool('resolve_matches', {'migration_id': mid, 'run_id': rid,
            'idempotency_key': 'verify-review-' + mid,
            'decisions': [{'mapping_id': item['mapping_id'], 'expected_revision': item['revision'],
                           'action': 'approve'} for item in listed['data']['items']]})
        self.assertEqual(decided['data']['applied'], 3, decided)
        artifact = self.tool('export_redirects', {'migration_id': mid, 'run_id': rid, 'format': 'json',
            'revision': decided['data']['selection_revision'], 'idempotency_key': 'verify-export-' + mid})
        self.assertEqual(artifact['status'], 'succeeded', artifact)
        aid = artifact['data']['artifact_id']
        verification = self.tool('verify_redirects', {'migration_id': mid, 'artifact_id': aid,
            'deployment_confirmation': True, 'live_origin': self.origins['new'], 'origin_rewrites': {},
            'idempotency_key': 'verify-check-' + mid})
        self.assertEqual(verification['status'], 'queued', verification)
        vid, did = verification['data']['verification_id'], verification['data']['deployment_id']
        items = self.sql('SELECT source_url,expected_url FROM migration_verification_items'
                         ' WHERE verification_id=%s ORDER BY ordinal', [vid])
        self.assertEqual(len(items), 3)
        self.assertEqual(sorted(row['source_url'] for row in items), sorted(urls['old']))
        self.assertTrue(all(len(row['source_url']) == URL_LENGTH for row in items))
        observed = asyncio.run(run_verification_batch(MigrationVerificationService(self.repo), 'long-probe', 50))
        self.assertEqual(observed['recorded'], 3, observed)
        self.assertTrue(any(len(path) > 4000 for _, path in self.origin_servers['old'].hits),
                        'the probe never requested a long URL')
        # 045 seeds sweep items from the same deployment scope and prices them by clicks.
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)"
                 " VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')", [mid, A, self.origins['old'] + '/'])
        self.sql('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,55,60)',
                 [mid, urls['old'][0]])
        now = datetime.now(timezone.utc)
        suffix = uuid4().hex
        paid = self.native.rpc('record_verified_subscription_period', {'p_user_id': A, 'p_subscription_id': 'sub_' + suffix,
            'p_customer_id': 'cus_' + suffix, 'p_sku': 'monitoring', 'p_status': 'active',
            'p_period_start': (now - timedelta(hours=1)).isoformat(), 'p_period_end': (now + timedelta(days=30)).isoformat(),
            'p_invoice_id': 'in_' + suffix, 'p_amount_cents': 2900, 'p_currency': 'usd', 'p_event_id': 'evt_' + suffix,
            'p_event_hash': 'a' * 64, 'p_event_at': now.isoformat(), 'p_livemode': False,
            'p_deployment_id': did}).execute().data
        monitor = self.tool('manage_monitoring', {'migration_id': mid, 'action': 'start', 'artifact_id': aid,
            'deployment_id': did, 'subscription_id': paid['subscription_id'],
            'idempotency_key': 'long-monitor-' + mid})
        self.assertEqual(monitor['data']['state'], 'active', monitor)
        self.assertEqual(asyncio.run(run_monitoring_batch(MigrationMonitoringService(self.repo),
                                                          'long-monitor-worker', 50))['recorded'], 3)
        sweep = self.sql("SELECT id FROM migration_verifications WHERE monitoring_id=%s AND kind='monitoring'",
                         [monitor['data']['monitoring_id']])
        self.assertEqual(len(sweep), 1, 'no monitoring sweep was scheduled')
        priced = self.sql('SELECT source_url,priority_clicks FROM migration_verification_items'
                          ' WHERE verification_id=%s ORDER BY priority_clicks DESC NULLS LAST', [sweep[0]['id']])
        self.assertEqual(len(priced), 3)
        self.assertEqual((priced[0]['source_url'], priced[0]['priority_clicks']), (urls['old'][0], 55))
        self.assertTrue(all(len(row['source_url']) == URL_LENGTH for row in priced))

    def test_capacity_contract_still_counts_rows_not_index_entries(self):
        from backend.services import job_limits
        self.assertEqual((job_limits.PIVOT_CONTENT_MAX_OLD_URLS, job_limits.PIVOT_CONTENT_MAX_NEW_URLS),
                         (15000, 20000))
        mid, inventories, urls = self.plan_import_long(2, seed='capacity')
        with patch('backend.services.migration_run_service.PIVOT_CONTENT_MAX_OLD_URLS', 1):
            blocked = self.tool('run_migration', {'migration_id': mid, 'old_inventory_id': inventories['old'],
                'new_inventory_id': inventories['new'], 'idempotency_key': 'capacity-' + mid})
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s', [mid])[0]['n'], 0,
                         blocked)
        allowed = self.tool('run_migration', {'migration_id': mid, 'old_inventory_id': inventories['old'],
            'new_inventory_id': inventories['new'], 'idempotency_key': 'capacity-ok-' + mid})
        self.assertEqual(allowed['status'], 'queued', allowed)
        self.assertEqual(len(self.sql('SELECT url FROM session_discovered_urls WHERE session_id=%s',
                                      [allowed['data']['session_id']])), 4)
