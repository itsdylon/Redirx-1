# 057 — persist unresolved mapping targets

Apply this new migration once, after the existing pivot packet, with its own
exact-body migration-history entry in the same transaction. When adding history
and scalar checks, remove only the file's outer BEGIN/COMMIT from the executed
wrapper; nested COMMIT would break atomicity. Store the original reviewed file
body in history. Do not replay or edit
032–056. The reviewed SQL file SHA-256 is
`7cf90fcaafbc898bf9f9439fda45cf3288117e993e4820e2c349d858b7c71994`.

The change drops `public.url_mappings.new_url`'s legacy NOT NULL constraint.
No row value, ACL, RLS policy, engine lease fence, grant, or run is changed. The
column becomes nullable for the table; existing legacy writers continue supplying
their normal destination. There is no fabricated redirect, empty-string target,
homepage fallback, or skipped unmatched source URL. Existing 038/050 behavior
already explicitly supports a NULL target as unresolved evidence.

The DDL takes a brief ACCESS EXCLUSIVE lock; its 5-second lock timeout fails closed
rather than waiting indefinitely on a busy production writer. Schema reload is
notified only on commit. If the operation times out, verify history and catalog
state before deciding on another attempt; do not blindly retry an uncertain
commit. Once NULL rows exist, restoring NOT NULL would require resolving or
removing evidence; that is not a safe rollback. Leaving this metadata-compatible
column nullable is the safe containment state.

## Why the healthy fixture reaches this path

The fixture's old and new homepages have different raw HTML, so no exact-HTML
match removes them first. PairingStage deliberately excludes old and new root
paths from semantic pairing. With `preserve_url_identity=True`, the old homepage
is retained as `new_url=NULL, confidence_score=0, match_type=unmatched,
needs_review=true`. This occurs even with healthy content and successful
embeddings. Root policy is unchanged. The error alone does not establish a scrape
or provider failure for any other page.

## Scalar evidence around the transaction

Run these before and after the ALTER, inside the same reviewed transaction, and
record only scalar results. The ALTER lock protects the after snapshot; to make
row-digest comparisons reliable under concurrent workers, acquire the table lock
before the baseline snapshot (with the same bounded lock timeout). Do not acquire
one lock and then release it between the baseline and change.

```sql
-- After BEGIN and SET LOCAL lock_timeout='5s':
LOCK TABLE public.url_mappings IN ACCESS EXCLUSIVE MODE;
SELECT attnotnull FROM pg_attribute
 WHERE attrelid='public.url_mappings'::regclass AND attname='new_url'
   AND NOT attisdropped;
SELECT count(*) AS rows, count(*) FILTER (WHERE new_url IS NULL) AS null_targets,
       md5(coalesce(string_agg(md5(to_jsonb(m)::text), '' ORDER BY id), '')) AS row_digest
 FROM public.url_mappings AS m;
SELECT md5(coalesce(relacl::text,'')) AS acl_digest,
       relrowsecurity, relforcerowsecurity
 FROM pg_class WHERE oid='public.url_mappings'::regclass;
SELECT md5(coalesce(string_agg(pg_get_triggerdef(oid), E'\n' ORDER BY tgname),'')) AS trigger_digest
 FROM pg_trigger WHERE tgrelid='public.url_mappings'::regclass AND NOT tgisinternal;
SELECT md5(coalesce(proacl::text,'')) AS rpc_acl_digest,
       md5(pg_get_functiondef(oid)) AS rpc_body_digest
 FROM pg_proc WHERE oid='public.persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean)'::regprocedure;
```

Expected: `attnotnull` true → false; all row counts/digests, ACL/RLS, trigger and
RPC metadata unchanged inside that transaction. The first post-commit worker can
then add genuine unresolved rows, so post-commit global row counts need not equal
the pre-commit counts. The history entry must contain the exact reviewed SQL body,
not just the filename. Check absence of the chosen history version before ALTER,
then independently read that exact version/name/body hash back after commit.

## Regression evidence

`backend/tests/test_unmatched_mapping_schema.py` creates a fresh disposable
loopback PostgreSQL database with the original NOT NULL column and real
030/032/034–038/050 dependencies. It proves SQLSTATE 23502 before applying ONLY
057; existing rows and ACL/RLS/trigger metadata remain identical; unresolved
writes and replay then succeed; list_matches classifies them as unmatched; direct
approve without a target is rejected; stale attempts, direct inserts and foreign
inventory URLs remain fenced. The same regression also passes with the complete native journey schema through
056, including 053–055 privilege/update/cascade fences. That harness uses JSONB
vectors and does not execute pgvector-only052. Existing engine
concurrency/reclaim tests also pass (six native tests total).
`test_content_capacity` independently exercises healthy non-identical homepages
and proves the root is the one unresolved row while the normal page pairs.
No test here proves actual provider quality or full-capacity production success.
