"""Full native journey plus hostile hosted defaults for new pivot objects.

Existing production credentials/schema are never read. The default-grant ACL
state is materialized on all actual pivot tables, including the 032 tables
created by the shared fixture before its extension hooks run.
"""
import os
from pathlib import Path
import re
import unittest
from backend.tests import test_pivot_product_journey as journey
from backend.tests.test_pivot_product_journey import ROOT, A, B

@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class PivotPrivilegeHardening(journey.NativeProductJourney):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import psycopg
        from psycopg import sql
        sources=[p.read_text() for p in (ROOT/'database/migrations').glob('*.sql')
                 if p.name[:3].isdigit() and 32<=int(p.name[:3])<=52 and int(p.name[:3])!=33]
        cls.pivot_tables=sorted(set(re.findall(r'CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)', '\n'.join(sources),re.I)))
        cls.pivot_functions=sorted(set(re.findall(r'CREATE(?: OR REPLACE)? FUNCTION\s+(?:public\.)?(\w+)', '\n'.join(sources),re.I)))
        with psycopg.connect(cls.dsn,autocommit=True) as c:
            c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO PUBLIC,anon,authenticated,service_role')
            c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO PUBLIC,anon,authenticated,service_role')
            c.execute('CREATE TABLE hosted_default_control(value text)')
            assert c.execute("SELECT has_table_privilege('anon','hosted_default_control','TRUNCATE')").fetchone()[0]
            cls.legacy_acl=c.execute("SELECT relname,relacl::text FROM pg_class WHERE relname IN ('migration_sessions','url_mappings','gsc_connections','account_usage_events') ORDER BY relname").fetchall()
            for table in cls.pivot_tables:
                c.execute(sql.SQL('GRANT ALL ON {} TO PUBLIC,anon,authenticated,service_role').format(sql.Identifier(table)))
                columns=[r[0] for r in c.execute('SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped',[table])]
                for privilege in ('SELECT','INSERT','UPDATE','REFERENCES'):
                    c.execute(sql.SQL('GRANT '+privilege+' ({}) ON {} TO PUBLIC,anon,authenticated,service_role').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table)))
            for signature, in c.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY(%s)",[cls.pivot_functions]).fetchall():
                c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION '+signature+' TO PUBLIC,anon,authenticated,service_role'))
            assert c.execute("SELECT has_table_privilege('anon','migration_operations','TRUNCATE')").fetchone()[0]
            migration=(ROOT/'database/migrations/053_pivot_privilege_hardening.sql').read_text()
            c.execute(migration)
            c.execute(migration) # Recoverable repeat does not drift privileges.

    def test_all_pivot_acl_surfaces_and_legacy_acl_preserved(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.dsn,autocommit=True) as c:
            self.assertEqual(c.execute("SELECT relname,relacl::text FROM pg_class WHERE relname IN ('migration_sessions','url_mappings','gsc_connections','account_usage_events') ORDER BY relname").fetchall(),self.legacy_acl)
            for table in self.pivot_tables:
                for role in ('anon','authenticated'):
                    for privilege in ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                        self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,%s)',[role,table,privilege]).fetchone()[0],(role,table,privilege))
                    for privilege in ('INSERT','UPDATE','REFERENCES'):
                        self.assertFalse(c.execute('SELECT has_any_column_privilege(%s,%s,%s)',[role,table,privilege]).fetchone()[0],(role,table,privilege))
                self.assertFalse(c.execute("SELECT has_any_column_privilege('anon',%s,'SELECT')",[table]).fetchone()[0],table)
                self.assertTrue(c.execute("SELECT has_table_privilege('service_role',%s,'SELECT')",[table]).fetchone()[0],table)
            for oid,name in c.execute("SELECT oid,proname FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY(%s)",[self.pivot_functions]):
                for role in ('anon','authenticated'):
                    self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",[role,oid]).fetchone()[0],(role,name))
            self.assertFalse(c.execute("SELECT has_table_privilege('service_role','migration_purchase_grants','INSERT')").fetchone()[0])
            self.assertFalse(c.execute("SELECT has_function_privilege('service_role','lock_migration_engine_attempt(uuid,uuid,text,integer)','EXECUTE')").fetchone()[0])
            self.assertTrue(c.execute("SELECT has_column_privilege('authenticated','migration_purchase_grants','state','SELECT')").fetchone()[0])
            self.assertFalse(c.execute("SELECT has_column_privilege('authenticated','migration_purchase_grants','stripe_payment_intent_id','SELECT')").fetchone()[0])
            mid=self.sql("INSERT INTO migration_records(user_id,old_origin,new_origin) VALUES(%s,'https://old.invalid','https://new.invalid') RETURNING id",[A])[0]['id']
            c.execute("SELECT set_config('request.jwt.claim.sub',%s,false)",[B]);c.execute('SET ROLE authenticated')
            self.assertEqual(c.execute('SELECT count(*) FROM migration_records WHERE id=%s',[mid]).fetchone()[0],0)
            c.execute('RESET ROLE');c.execute("SELECT set_config('request.jwt.claim.sub',%s,false)",[A]);c.execute('SET ROLE authenticated')
            self.assertEqual(c.execute('SELECT count(*) FROM migration_records WHERE id=%s',[mid]).fetchone()[0],1)
            for role in ('authenticated','anon'):
                c.execute('RESET ROLE');c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
                with self.assertRaisesRegex(psycopg.Error,'permission denied'):
                    c.execute('TRUNCATE migration_operations')
            c.execute('RESET ROLE')
