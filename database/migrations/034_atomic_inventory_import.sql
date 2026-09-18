-- 034: Atomic explicit-import publication. Requires dormant migration 032.
-- No routes/tools are wired to this function by this migration.
BEGIN;

CREATE OR REPLACE FUNCTION publish_inventory_import(
  p_user_id UUID, p_migration_id UUID, p_idempotency_key TEXT, p_inventory JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_migration migration_records%ROWTYPE;
  v_operation JSONB;
  v_snapshot UUID;
  v_side TEXT;
  v_origin TEXT;
  v_status TEXT;
  v_item JSONB;
  v_result JSONB;
  v_count INTEGER;
  v_input INTEGER;
  v_excluded INTEGER;
  v_deduplicated INTEGER;
  v_variants INTEGER;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_idempotency_key IS NULL
     OR btrim(p_idempotency_key) = '' OR length(p_idempotency_key) > 200
     OR p_idempotency_key ~ '[[:cntrl:]]' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  -- Serialize publication per migration and check ownership before inspecting
  -- user content. A different user's ID is indistinguishable from a missing ID.
  SELECT * INTO v_migration FROM migration_records
    WHERE id = p_migration_id AND user_id = p_user_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  IF jsonb_typeof(p_inventory) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  IF octet_length(p_inventory::text) > 33554432 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  v_side := p_inventory->>'side';
  v_status := p_inventory->>'status';
  IF v_side IS NULL OR v_side NOT IN ('old', 'new')
     OR v_status IS NULL OR v_status NOT IN ('complete', 'partial')
     OR p_inventory->>'policy_version' IS DISTINCT FROM 'explicit_inventory_v1'
     OR coalesce(p_inventory->>'content_hash', '') !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_inventory->'items') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'origins') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'exclusions') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'coverage') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_origin := CASE v_side WHEN 'old' THEN v_migration.old_origin ELSE v_migration.new_origin END;
  -- Existing legacy placeholders cannot silently acquire a guessed origin.
  IF v_origin IS NULL OR NOT (p_inventory->'origins' ? rtrim(v_origin, '/'))
     OR jsonb_array_length(p_inventory->'origins') NOT BETWEEN 1 AND 200
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_inventory->'origins') o
       WHERE jsonb_typeof(o) <> 'string' OR length(o #>> '{}') > 2048) THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_count := jsonb_array_length(p_inventory->'items');
  v_excluded := jsonb_array_length(p_inventory->'exclusions');
  IF v_count > 50000 OR v_excluded > 50000 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  IF p_inventory#>>'{coverage,kind}' IS DISTINCT FROM 'explicit_import'
     OR p_inventory#>'{coverage,network_checked}' IS DISTINCT FROM 'false'::jsonb
     OR p_inventory#>'{coverage,site_coverage_claimed}' IS DISTINCT FROM 'false'::jsonb
     OR p_inventory#>'{coverage,unique_count}' IS DISTINCT FROM to_jsonb(v_count)
     OR p_inventory#>'{coverage,excluded_count}' IS DISTINCT FROM to_jsonb(v_excluded)
     OR jsonb_typeof(p_inventory#>'{coverage,input_count}') IS DISTINCT FROM 'number'
     OR jsonb_typeof(p_inventory#>'{coverage,deduplicated_count}') IS DISTINCT FROM 'number'
     OR coalesce(p_inventory#>>'{coverage,input_count}', '') !~ '^[0-9]{1,5}$'
     OR coalesce(p_inventory#>>'{coverage,deduplicated_count}', '') !~ '^[0-9]{1,5}$' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_input := (p_inventory#>>'{coverage,input_count}')::integer;
  v_deduplicated := (p_inventory#>>'{coverage,deduplicated_count}')::integer;
  IF v_input > 50000 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  IF v_input <> v_count + v_excluded + v_deduplicated
     OR (v_status = 'complete') IS DISTINCT FROM (v_count > 0 AND v_excluded = 0)
     OR p_inventory#>'{coverage,complete}' IS DISTINCT FROM to_jsonb(v_status = 'complete') THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  -- Idempotency uses the complete JSONB value, never a caller-provided digest.
  v_operation := reserve_migration_operation(
    p_user_id, p_migration_id, 'import_inventory', p_idempotency_key,
    encode(sha256(convert_to(p_inventory::text, 'UTF8')), 'hex')
  );
  IF (v_operation->>'replayed')::boolean THEN
    IF v_operation->>'status' = 'succeeded'
       AND jsonb_typeof(v_operation->'result') = 'object'
       AND v_operation->'result' ? 'inventory_id' THEN
      RETURN (v_operation->'result') || jsonb_build_object('replayed', true);
    END IF;
    RAISE EXCEPTION 'inventory_busy' USING ERRCODE = 'P0001';
  END IF;
  IF EXISTS (SELECT 1 FROM inventory_snapshots
    WHERE migration_id = p_migration_id AND side = v_side AND status = 'pending') THEN
    RAISE EXCEPTION 'inventory_busy' USING ERRCODE = 'P0001';
  END IF;

  -- All validation and writes are one transaction; any error rolls back the
  -- reservation too. Partial means a published, immutable incomplete import.
  INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES (p_migration_id,p_user_id,v_side,p_inventory->>'policy_version')
    RETURNING id INTO v_snapshot;

  FOR v_item IN SELECT value FROM jsonb_array_elements(p_inventory->'items') LOOP
    IF jsonb_typeof(v_item) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_item->'count_key') IS DISTINCT FROM 'string'
       OR length(v_item->>'count_key') NOT BETWEEN 1 AND 8192
       OR v_item->>'count_key' IS DISTINCT FROM v_item->>'canonical_url'
       OR jsonb_typeof(v_item->'original_url') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_item->'original_urls') IS DISTINCT FROM 'array'
       OR jsonb_typeof(v_item->'provenance') IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    IF jsonb_array_length(v_item->'original_urls') NOT BETWEEN 1 AND 50000
       OR jsonb_array_length(v_item->'provenance') NOT BETWEEN 1 AND 1600000
       OR NOT (v_item->'original_urls' ? (v_item->>'original_url'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_item->'original_urls') u
         WHERE jsonb_typeof(u) <> 'string' OR length(u #>> '{}') NOT BETWEEN 1 AND 8192)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_item->'provenance') s
         WHERE jsonb_typeof(s) <> 'string' OR length(btrim(s #>> '{}')) NOT BETWEEN 1 AND 256)
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_inventory->'origins') o
         WHERE left(v_item->>'count_key', length(o) + 1) = o || '/') THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    INSERT INTO session_discovered_urls(inventory_id,session_id,side,url,count_key,sources)
      SELECT v_snapshot,NULL,v_side,u,v_item->>'count_key',
        ARRAY(SELECT jsonb_array_elements_text(v_item->'provenance'))
      FROM jsonb_array_elements_text(v_item->'original_urls') u;
  END LOOP;
  SELECT count(*) INTO v_variants FROM session_discovered_urls WHERE inventory_id = v_snapshot;
  IF v_variants > v_input - v_excluded
     OR (SELECT count(DISTINCT count_key) FROM session_discovered_urls WHERE inventory_id = v_snapshot) <> v_count THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  UPDATE inventory_snapshots SET status = v_status,
    content_hash = p_inventory->>'content_hash', page_count = v_count,
    coverage = (p_inventory->'coverage') || jsonb_build_object('declared_origins', p_inventory->'origins'),
    exclusions = p_inventory->'exclusions', completed_at = now()
    WHERE id = v_snapshot;
  v_result := jsonb_build_object(
    'operation_id',v_operation->>'id', 'inventory_id',v_snapshot,
    'migration_id',p_migration_id, 'side',v_side, 'status',v_status,
    'page_count',v_count, 'content_hash',p_inventory->>'content_hash', 'replayed',false
  );
  UPDATE migration_operations SET status = 'succeeded', result = v_result
    WHERE id = (v_operation->>'id')::uuid;
  RETURN v_result;
EXCEPTION WHEN unique_violation THEN
  -- Duplicate URL variants are malformed input, not a partial publication.
  RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
END;
$$;

REVOKE ALL ON FUNCTION publish_inventory_import(UUID,UUID,TEXT,JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION publish_inventory_import(UUID,UUID,TEXT,JSONB) TO service_role;
COMMIT;
