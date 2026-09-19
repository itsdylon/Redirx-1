-- 038: durable, audited match-review decisions.
-- Requires 032 and the legitimate session bridge created by 037.
BEGIN;

CREATE TABLE IF NOT EXISTS migration_mapping_decisions (
  run_id UUID NOT NULL,
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  mapping_id UUID NOT NULL REFERENCES url_mappings(id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
  action TEXT NOT NULL CHECK (action IN (
    'approve', 'accept_repair', 'set_target', 'reject', 'defer', 'intentional_removal'
  )),
  target_url TEXT,
  actor UUID NOT NULL REFERENCES user_profiles(id) ON DELETE RESTRICT,
  rationale TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, mapping_id),
  UNIQUE (run_id, mapping_id, migration_id, user_id),
  FOREIGN KEY (run_id, migration_id, user_id)
    REFERENCES migration_runs(id, migration_id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mapping_decisions_run_revision
  ON migration_mapping_decisions(run_id, revision DESC, mapping_id);

CREATE TABLE IF NOT EXISTS migration_mapping_decision_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL,
  mapping_id UUID NOT NULL,
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  operation_id UUID NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  action TEXT NOT NULL CHECK (action IN (
    'approve', 'accept_repair', 'set_target', 'reject', 'defer', 'intentional_removal'
  )),
  actor UUID NOT NULL REFERENCES user_profiles(id) ON DELETE RESTRICT,
  rationale TEXT,
  prior_values JSONB NOT NULL CHECK (jsonb_typeof(prior_values) = 'object'),
  resulting_values JSONB NOT NULL CHECK (jsonb_typeof(resulting_values) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, mapping_id, revision),
  FOREIGN KEY (run_id, mapping_id, migration_id, user_id)
    REFERENCES migration_mapping_decisions(run_id, mapping_id, migration_id, user_id)
    ON DELETE CASCADE,
  FOREIGN KEY (operation_id, migration_id, user_id)
    REFERENCES migration_operations(id, migration_id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mapping_decision_events_run_mapping
  ON migration_mapping_decision_events(run_id, mapping_id, revision DESC);

CREATE OR REPLACE FUNCTION prevent_mapping_decision_event_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  -- Keep account/migration deletion viable, but never permit a direct audit
  -- rewrite or a nested application trigger to bypass immutability.
  IF TG_OP = 'DELETE' AND NOT EXISTS (
    SELECT 1 FROM migration_records
    WHERE id = OLD.migration_id AND user_id = OLD.user_id
  ) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'mapping decision events are immutable';
END;
$$;
DROP TRIGGER IF EXISTS mapping_decision_events_immutable ON migration_mapping_decision_events;
CREATE TRIGGER mapping_decision_events_immutable
  BEFORE UPDATE OR DELETE ON migration_mapping_decision_events
  FOR EACH ROW EXECUTE FUNCTION prevent_mapping_decision_event_mutation();

-- Scoped read with stable traffic-first ordering. traffic_observed is separate
-- from clicks so an absent metric never masquerades as an observed zero.
CREATE OR REPLACE FUNCTION list_migration_matches(
  p_user_id UUID, p_migration_id UUID, p_run_id UUID,
  p_filter TEXT DEFAULT 'all', p_cursor JSONB DEFAULT NULL, p_limit INTEGER DEFAULT 100
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_run migration_runs%ROWTYPE;
  v_cursor_observed BOOLEAN;
  v_cursor_clicks BIGINT;
  v_cursor_id UUID;
  v_rows JSONB;
  v_next JSONB;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_run_id IS NULL
     OR p_filter NOT IN ('all','needs_review','unmatched','approved','rejected')
     OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  SELECT * INTO v_run FROM migration_runs
    WHERE id=p_run_id AND migration_id=p_migration_id AND user_id=p_user_id
    FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  IF v_run.legacy_session_id IS NULL OR v_run.new_inventory_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM inventory_snapshots i
       WHERE i.id=v_run.new_inventory_id AND i.migration_id=p_migration_id
         AND i.user_id=p_user_id AND i.side='new' AND i.status='complete') THEN
    RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001';
  END IF;
  IF p_cursor IS NOT NULL THEN
    IF jsonb_typeof(p_cursor) <> 'object'
       OR jsonb_typeof(p_cursor->'observed') <> 'boolean'
       OR coalesce(p_cursor->>'clicks','') !~ '^-?[0-9]+$'
       OR coalesce(p_cursor->>'id','') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    v_cursor_observed := (p_cursor->>'observed')::boolean;
    v_cursor_clicks := (p_cursor->>'clicks')::bigint;
    v_cursor_id := (p_cursor->>'id')::uuid;
  END IF;
  WITH scoped AS (
    SELECT m.*, d.revision, d.action AS decision_action, d.target_url AS decision_target,
      COALESCE(d.action, CASE WHEN m.new_url IS NULL THEN 'unmatched'
        WHEN m.needs_review THEN 'needs_review' ELSE 'approved' END) AS review_status,
      t.observed, t.clicks
    FROM url_mappings m
    LEFT JOIN migration_mapping_decisions d ON d.run_id=v_run.id AND d.mapping_id=m.id
    LEFT JOIN LATERAL (
      SELECT coalesce(bool_or(s.clicks IS NOT NULL OR s.impressions IS NOT NULL), false) AS observed,
        sum(s.clicks)::bigint AS clicks
      FROM session_discovered_urls s
      WHERE s.session_id=v_run.legacy_session_id AND s.side='old' AND s.url=m.old_url
    ) t ON true
    WHERE m.session_id=v_run.legacy_session_id
  ), filtered AS (
    SELECT * FROM scoped WHERE p_filter='all'
      OR (p_filter='needs_review' AND review_status IN ('needs_review','defer'))
      OR (p_filter='unmatched' AND review_status='unmatched')
      OR (p_filter='approved' AND review_status IN ('approved','approve','accept_repair','set_target'))
      OR (p_filter='rejected' AND review_status IN ('reject','intentional_removal'))
  ), paged AS (
    SELECT * FROM filtered
    WHERE p_cursor IS NULL OR observed < v_cursor_observed
      OR (observed = v_cursor_observed AND coalesce(clicks,-1) < v_cursor_clicks)
      OR (observed = v_cursor_observed AND coalesce(clicks,-1) = v_cursor_clicks AND id > v_cursor_id)
    ORDER BY observed DESC, clicks DESC NULLS LAST, id ASC
    LIMIT p_limit + 1
  ), visible AS (SELECT * FROM paged LIMIT p_limit), tail AS (SELECT * FROM paged OFFSET p_limit LIMIT 1)
  SELECT coalesce(jsonb_agg(jsonb_build_object(
    'mapping_id',id,'old_url',old_url,'new_url',new_url,'confidence_score',confidence_score,
    'needs_review',needs_review,'repair',CASE WHEN repaired_url IS NULL THEN NULL ELSE jsonb_build_object(
      'url',repaired_url,'method',repair_method,'confidence',repair_confidence,
      'support',repair_support,'evidence',repair_evidence) END,
    'revision',coalesce(revision,0),'decision',decision_action,'decision_target',decision_target,
    'review_status',review_status,'traffic_observed',observed,'traffic_clicks',clicks
  ) ORDER BY observed DESC, clicks DESC NULLS LAST, id ASC),'[]'::jsonb),
    CASE WHEN EXISTS (SELECT 1 FROM tail) THEN (
      SELECT jsonb_build_object('observed',observed,'clicks',coalesce(clicks,-1),'id',id)
      FROM visible
      ORDER BY observed ASC, clicks ASC NULLS FIRST, id DESC
      LIMIT 1
    ) END
    INTO v_rows, v_next
    FROM visible;
  RETURN jsonb_build_object('items',v_rows,'next_cursor',v_next);
END;
$$;

CREATE OR REPLACE FUNCTION resolve_migration_match_decisions(
  p_user_id UUID, p_migration_id UUID, p_run_id UUID, p_actor UUID,
  p_idempotency_key TEXT, p_decisions JSONB
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_run migration_runs%ROWTYPE;
  v_operation JSONB;
  v_operation_id UUID;
  v_item JSONB;
  v_mapping url_mappings%ROWTYPE;
  v_decision migration_mapping_decisions%ROWTYPE;
  v_mapping_id UUID;
  v_expected INTEGER;
  v_action TEXT;
  v_target TEXT;
  v_rationale TEXT;
  v_prior JSONB;
  v_result JSONB;
  v_outcomes JSONB := '[]'::jsonb;
  v_seen UUID[] := ARRAY[]::uuid[];
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_run_id IS NULL OR p_actor IS NULL
     OR p_actor <> p_user_id OR p_idempotency_key IS NULL
     OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
     OR p_idempotency_key ~ '[[:cntrl:]]'
     OR jsonb_typeof(p_decisions) <> 'array' OR jsonb_array_length(p_decisions) NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  SELECT * INTO v_run FROM migration_runs
    WHERE id=p_run_id AND migration_id=p_migration_id AND user_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  IF v_run.legacy_session_id IS NULL OR v_run.new_inventory_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM migration_sessions s
       WHERE s.id=v_run.legacy_session_id AND s.user_id=p_user_id::text)
     OR NOT EXISTS (SELECT 1 FROM inventory_snapshots i
       WHERE i.id=v_run.new_inventory_id AND i.migration_id=p_migration_id
         AND i.user_id=p_user_id AND i.side='new' AND i.status='complete') THEN
    RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001';
  END IF;
  v_operation := reserve_migration_operation(p_user_id,p_migration_id,'resolve_matches',p_idempotency_key,
    encode(sha256(convert_to(jsonb_build_object('run_id',p_run_id,'actor',p_actor,'decisions',p_decisions)::text,'UTF8')),'hex'));
  IF (v_operation->>'replayed')::boolean THEN
    IF v_operation->>'status'='succeeded' AND jsonb_typeof(v_operation->'result')='object' THEN
      RETURN (v_operation->'result') || jsonb_build_object('replayed',true);
    END IF;
    RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
  END IF;
  v_operation_id := (v_operation->>'id')::uuid;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_decisions) LOOP
    BEGIN
      IF jsonb_typeof(v_item)<>'object'
         OR coalesce(v_item->>'mapping_id','') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
         OR jsonb_typeof(v_item->'expected_revision')<>'number'
         OR coalesce(v_item->>'expected_revision','') !~ '^[0-9]+$'
         OR v_item->>'action' NOT IN ('approve','accept_repair','set_target','reject','defer','intentional_removal')
         OR (v_item ? 'rationale' AND (jsonb_typeof(v_item->'rationale')<>'string' OR length(v_item->>'rationale')>2000)) THEN
        RAISE EXCEPTION 'invalid_input';
      END IF;
      v_mapping_id := (v_item->>'mapping_id')::uuid;
      v_expected := (v_item->>'expected_revision')::integer;
      v_action := v_item->>'action';
      v_rationale := NULLIF(v_item->>'rationale','');
      IF v_mapping_id = ANY(v_seen) THEN RAISE EXCEPTION 'invalid_input'; END IF;
      v_seen := array_append(v_seen,v_mapping_id);
      SELECT * INTO v_mapping FROM url_mappings
        WHERE id=v_mapping_id AND session_id=v_run.legacy_session_id FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'not_found'; END IF;
      SELECT * INTO v_decision FROM migration_mapping_decisions
        WHERE run_id=p_run_id AND mapping_id=v_mapping_id FOR UPDATE;
      IF FOUND AND v_decision.revision<>v_expected THEN RAISE EXCEPTION 'revision_conflict'; END IF;
      IF NOT FOUND AND v_expected<>0 THEN RAISE EXCEPTION 'revision_conflict'; END IF;
      v_prior := jsonb_build_object('revision',coalesce(v_decision.revision,0),'action',v_decision.action,
        'target_url',v_decision.target_url,'mapping_new_url',v_mapping.new_url,'needs_review',v_mapping.needs_review);
      IF v_action='set_target' THEN
        IF jsonb_typeof(v_item->'target_url')<>'string' OR length(btrim(v_item->>'target_url')) NOT BETWEEN 1 AND 8192 THEN
          RAISE EXCEPTION 'invalid_input'; END IF;
        v_target := btrim(v_item->>'target_url');
      ELSIF v_action='accept_repair' THEN
        IF v_item ? 'target_url' OR v_mapping.repaired_url IS NULL OR v_mapping.repair_method IS NULL
           OR v_mapping.repair_confidence IS NULL OR coalesce(v_mapping.repair_support,0)<=0
           OR nullif(btrim(v_mapping.repair_evidence),'') IS NULL THEN RAISE EXCEPTION 'invalid_input'; END IF;
        v_target := v_mapping.repaired_url;
      ELSIF v_action='approve' THEN
        IF v_item ? 'target_url' OR v_mapping.needs_review OR v_mapping.new_url IS NULL THEN
          RAISE EXCEPTION 'invalid_input'; END IF;
        v_target := v_mapping.new_url;
      ELSE
        IF v_item ? 'target_url' THEN RAISE EXCEPTION 'invalid_input'; END IF;
        v_target := NULL;
      END IF;
      IF v_target IS NOT NULL AND NOT EXISTS (SELECT 1 FROM session_discovered_urls
        WHERE inventory_id=v_run.new_inventory_id AND url=v_target) THEN
        RAISE EXCEPTION 'invalid_input';
      END IF;
      INSERT INTO migration_mapping_decisions(run_id,migration_id,user_id,mapping_id,revision,action,target_url,actor,rationale)
        VALUES(p_run_id,p_migration_id,p_user_id,v_mapping_id,1,v_action,v_target,p_actor,v_rationale)
      ON CONFLICT(run_id,mapping_id) DO UPDATE SET revision=migration_mapping_decisions.revision+1,
        action=EXCLUDED.action,target_url=EXCLUDED.target_url,actor=EXCLUDED.actor,rationale=EXCLUDED.rationale,updated_at=now()
      RETURNING * INTO v_decision;
      v_result := jsonb_build_object('mapping_id',v_mapping_id,'revision',v_decision.revision,
        'action',v_decision.action,'target_url',v_decision.target_url,'code','ok');
      INSERT INTO migration_mapping_decision_events(run_id,mapping_id,migration_id,user_id,operation_id,revision,action,actor,rationale,prior_values,resulting_values)
        VALUES(p_run_id,v_mapping_id,p_migration_id,p_user_id,v_operation_id,v_decision.revision,v_action,p_actor,v_rationale,v_prior,v_result);
      v_outcomes := v_outcomes || jsonb_build_array(v_result);
    EXCEPTION WHEN OTHERS THEN
      IF SQLERRM IN ('invalid_input','not_found','revision_conflict') THEN
        v_outcomes := v_outcomes || jsonb_build_array(jsonb_build_object('mapping_id',v_item->>'mapping_id','code',SQLERRM));
      ELSE RAISE; END IF;
    END;
  END LOOP;
  v_result := jsonb_build_object('migration_id',p_migration_id,'run_id',p_run_id,
    'operation_id',v_operation_id,'outcomes',v_outcomes,'replayed',false);
  UPDATE migration_operations SET status='succeeded',result=v_result WHERE id=v_operation_id;
  RETURN v_result;
END;
$$;

ALTER TABLE migration_mapping_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_mapping_decision_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY mapping_decisions_select_own ON migration_mapping_decisions
  FOR SELECT TO authenticated USING (user_id=auth.uid());
CREATE POLICY mapping_decision_events_select_own ON migration_mapping_decision_events
  FOR SELECT TO authenticated USING (user_id=auth.uid());
REVOKE ALL ON migration_mapping_decisions,migration_mapping_decision_events FROM anon,authenticated;
GRANT SELECT ON migration_mapping_decisions,migration_mapping_decision_events TO authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON migration_mapping_decisions,migration_mapping_decision_events TO service_role;
REVOKE ALL ON FUNCTION list_migration_matches(UUID,UUID,UUID,TEXT,JSONB,INTEGER) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION list_migration_matches(UUID,UUID,UUID,TEXT,JSONB,INTEGER) TO service_role;
REVOKE ALL ON FUNCTION resolve_migration_match_decisions(UUID,UUID,UUID,UUID,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION resolve_migration_match_decisions(UUID,UUID,UUID,UUID,TEXT,JSONB) TO service_role;
COMMIT;
