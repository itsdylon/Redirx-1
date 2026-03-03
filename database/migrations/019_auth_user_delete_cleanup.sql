-- Migration 019: One-click auth user deletion cleanup
-- Purpose:
--   1) Allow deleting users directly from auth.users without FK failures.
--   2) Delete user-owned migration data when auth user is deleted.
--   3) Convert non-owned auth references to ON DELETE SET NULL.

-- ============================================================================
-- 1) BEFORE DELETE trigger on auth.users for owned data cleanup
-- ============================================================================

CREATE OR REPLACE FUNCTION public.handle_auth_user_deleted()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Delete user-owned session artifacts (children first, then parent).
  IF to_regclass('public.migration_sessions') IS NOT NULL THEN
    IF to_regclass('public.url_mappings') IS NOT NULL THEN
      EXECUTE $sql$
        DELETE FROM public.url_mappings
        WHERE session_id IN (
          SELECT id
          FROM public.migration_sessions
          WHERE user_id = $1
        )
      $sql$
      USING OLD.id::text;
    END IF;

    IF to_regclass('public.webpage_embeddings') IS NOT NULL THEN
      EXECUTE $sql$
        DELETE FROM public.webpage_embeddings
        WHERE session_id IN (
          SELECT id
          FROM public.migration_sessions
          WHERE user_id = $1
        )
      $sql$
      USING OLD.id::text;
    END IF;

    EXECUTE $sql$
      DELETE FROM public.migration_sessions
      WHERE user_id = $1
    $sql$
    USING OLD.id::text;
  END IF;

  -- Remove history rows where actor is the deleted user.
  IF to_regclass('public.invite_events') IS NOT NULL THEN
    EXECUTE $sql$
      DELETE FROM public.invite_events
      WHERE actor_id = $1
    $sql$
    USING OLD.id;
  END IF;

  -- For shared/admin tables, clear references so records can remain.
  IF to_regclass('public.trial_campaigns') IS NOT NULL THEN
    EXECUTE $sql$
      UPDATE public.trial_campaigns
      SET owner_user_id = NULL
      WHERE owner_user_id = $1
    $sql$
    USING OLD.id;
  END IF;

  IF to_regclass('public.trial_invites') IS NOT NULL THEN
    EXECUTE $sql$
      UPDATE public.trial_invites
      SET created_by_user_id = NULL
      WHERE created_by_user_id = $1
    $sql$
    USING OLD.id;

    EXECUTE $sql$
      UPDATE public.trial_invites
      SET redeemed_by_user_id = NULL
      WHERE redeemed_by_user_id = $1
    $sql$
    USING OLD.id;
  END IF;

  IF to_regclass('public.founder_waitlist') IS NOT NULL THEN
    EXECUTE $sql$
      UPDATE public.founder_waitlist
      SET approved_by_user_id = NULL
      WHERE approved_by_user_id = $1
    $sql$
    USING OLD.id;
  END IF;

  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_deleted ON auth.users;
CREATE TRIGGER on_auth_user_deleted
  BEFORE DELETE ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_auth_user_deleted();

-- ============================================================================
-- 2) Normalize auth.users foreign keys to ON DELETE SET NULL (non-owned refs)
-- ============================================================================

DO $$
DECLARE
  fk_record RECORD;
BEGIN
  FOR fk_record IN
    SELECT
      c.conname AS constraint_name,
      ns.nspname AS schema_name,
      tbl.relname AS table_name,
      att.attname AS column_name
    FROM pg_constraint c
    JOIN pg_class tbl
      ON tbl.oid = c.conrelid
    JOIN pg_namespace ns
      ON ns.oid = tbl.relnamespace
    JOIN pg_class ref_tbl
      ON ref_tbl.oid = c.confrelid
    JOIN pg_namespace ref_ns
      ON ref_ns.oid = ref_tbl.relnamespace
    JOIN unnest(c.conkey) AS key_cols(attnum)
      ON TRUE
    JOIN pg_attribute att
      ON att.attrelid = tbl.oid
     AND att.attnum = key_cols.attnum
    WHERE c.contype = 'f'
      AND ns.nspname = 'public'
      AND ref_ns.nspname = 'auth'
      AND ref_tbl.relname = 'users'
      AND att.attname IN (
        'owner_user_id',
        'created_by_user_id',
        'redeemed_by_user_id',
        'approved_by_user_id'
      )
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.%I DROP CONSTRAINT %I',
      fk_record.schema_name,
      fk_record.table_name,
      fk_record.constraint_name
    );

    EXECUTE format(
      'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES auth.users(id) ON DELETE SET NULL',
      fk_record.schema_name,
      fk_record.table_name,
      fk_record.constraint_name,
      fk_record.column_name
    );
  END LOOP;
END;
$$;

-- Verification (optional):
-- SELECT trigger_name
-- FROM information_schema.triggers
-- WHERE trigger_schema = 'auth'
--   AND event_object_table = 'users'
--   AND trigger_name = 'on_auth_user_deleted';
