-- Unresolved pivot mappings retain their old URL and have no destination.
-- 038 already classifies NULL targets as unmatched; 050 already permits them
-- through the owned run/lease write fence. The legacy column's NOT NULL
-- constraint prevented that existing contract from reaching persistence.
-- Metadata only: no rows, grants, policies, or engine fences are changed.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
ALTER TABLE public.url_mappings ALTER COLUMN new_url DROP NOT NULL;
NOTIFY pgrst, 'reload schema';
COMMIT;
