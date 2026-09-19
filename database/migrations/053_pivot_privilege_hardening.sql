-- 053: Explicit pivot privileges under broad hosted default grants.
-- Apply after 052. Only new pivot tables/functions are changed. Existing
-- legacy sessions/mappings/GSC storage, account_usage_events and 033 stay intact.
-- RLS does not govern TRUNCATE, and table REVOKE does not remove column grants.
BEGIN;
DO $hardening$
DECLARE target text; columns_sql text; f record;
BEGIN
 FOREACH target IN ARRAY ARRAY[
  'artifact_deployments','gsc_agent_accounts','gsc_agent_operations','gsc_migration_metrics',
  'gsc_migration_selections','inventory_snapshots','migration_artifact_contents','migration_artifact_mutations',
  'migration_artifacts','migration_discovery_jobs','migration_mapping_decision_events','migration_mapping_decisions',
  'migration_monitor_alerts','migration_monitor_issues','migration_monitors','migration_operations',
  'migration_price_policies','migration_price_quotes','migration_purchase_grants','migration_records',
  'migration_run_failure_receipts','migration_runs','migration_studio_slots','migration_studio_work_reservations',
  'migration_subscription_checkout_events','migration_subscription_checkout_keys','migration_subscription_checkouts','migration_subscription_customers',
  'migration_subscription_site_slots','migration_test_checkout_events','migration_test_checkouts','migration_test_subscription_events',
  'migration_test_subscription_periods','migration_test_subscriptions','migration_verification_items','migration_verification_request_keys',
  'migration_verifications'
 ] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC,anon,authenticated,service_role',target);
  SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) INTO columns_sql
   FROM pg_attribute WHERE attrelid=format('public.%I',target)::regclass AND attnum>0 AND NOT attisdropped;
  EXECUTE format('REVOKE SELECT (%s),INSERT (%s),UPDATE (%s),REFERENCES (%s) ON TABLE public.%I FROM PUBLIC,anon,authenticated,service_role',
   columns_sql,columns_sql,columns_sql,columns_sql,target);
  -- Financial/queue authority uses SECURITY DEFINER RPCs, not raw backend DML.
  IF target=ANY(ARRAY[
  'migration_discovery_jobs','migration_price_policies','migration_price_quotes','migration_purchase_grants',
  'migration_run_failure_receipts','migration_studio_slots','migration_studio_work_reservations','migration_subscription_checkout_events',
  'migration_subscription_checkout_keys','migration_subscription_checkouts','migration_subscription_customers','migration_subscription_site_slots',
  'migration_test_checkout_events','migration_test_checkouts','migration_test_subscription_events','migration_test_subscription_periods',
  'migration_test_subscriptions'
 ]) THEN
   EXECUTE format('GRANT SELECT ON TABLE public.%I TO service_role',target);
  ELSE
   EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.%I TO service_role',target);
  END IF;
  IF target=ANY(ARRAY[
  'migration_records','inventory_snapshots','migration_runs','migration_artifacts',
  'migration_operations','migration_price_quotes','migration_mapping_decisions','migration_mapping_decision_events',
  'artifact_deployments'
 ]) THEN
   EXECUTE format('GRANT SELECT ON TABLE public.%I TO authenticated',target);
  END IF;
 END LOOP;
 -- Payment identifiers remain private; restore only 036's reviewed owner view.
 GRANT SELECT(id,user_id,migration_id,quote_id,source,state,created_at,
  first_successful_paid_run_at,first_successful_paid_run_id,rerun_expires_at)
  ON public.migration_purchase_grants TO authenticated;
 -- A prior broad EXECUTE default must not expose helper or mutation routines.
 FOR f IN SELECT oid::regprocedure AS signature,proname FROM pg_proc
  WHERE pronamespace='public'::regnamespace AND proname=ANY(ARRAY[
  'activate_migration_monitor','advance_migration_selection_revision','apply_verified_migration_test_checkout_event','attach_migration_test_checkout',
  'attach_subscription_checkout','attach_subscription_customer','authorize_migration_run_dispatch','checkpoint_inventory_discovery',
  'claim_inventory_discovery','claim_monitor_alert','claim_monitoring_batch','claim_next_job',
  'claim_verification_batch','complete_studio_migration_work','complete_verification_item','consume_gsc_agent_state',
  'create_migration_price_quote','delete_durable_migrations_before_auth_user','deny_included_verification','erase_gsc_agent_credentials',
  'expire_verified_subscription_checkout','finalize_migration_infrastructure_failure','finalize_migration_run_session','finish_gsc_agent_operation',
  'finish_monitor_alert','get_migration_price_quote','get_migration_purchase_grant','get_migration_selection_revision',
  'get_migration_test_checkout','get_migration_test_subscription','get_studio_work_reservation','get_subscription_checkout',
  'get_subscription_monitoring_site','guard_migration_engine_insert','included_verification_entitled','issue_free_migration_grant',
  'list_migration_matches','lock_migration_engine_attempt','manage_migration_monitor','match_migration_pages',
  'migration_grant_summary','migration_quote_summary','migration_subscription_summary','migration_test_checkout_summary',
  'monitor_alert_counts','monitor_entitlement','monitor_observed_clicks','monitor_verified_email',
  'persist_migration_run_embedding','persist_migration_run_mapping','plan_migration','preserve_mcp_session_inputs',
  'preserve_migration_checkout_bindings','preserve_migration_operation_identity','preserve_migration_price_bindings','preserve_monitor_scope',
  'preserve_run_failure_receipt','preserve_subscription_authority','preserve_subscription_checkout_scope','preserve_verification_scope',
  'prevent_artifact_deployment_identity_mutation','prevent_mapping_decision_event_mutation','prevent_migration_artifact_content_mutation','prevent_migration_artifact_mutation',
  'prevent_published_inventory_mutation','prevent_published_inventory_url_mutation','publish_artifact_deployment','publish_inventory_import',
  'publish_migration_artifact','reconcile_monitor_sweep','record_migration_grant_success','record_monitoring_item',
  'record_verified_subscription_checkout_period','record_verified_subscription_period','record_verified_test_migration_payment','refresh_migration_gsc_access_token',
  'refresh_migration_plan_readiness','release_failed_studio_migration_work','reserve_gsc_agent_operation','reserve_included_verification',
  'reserve_migration_operation','reserve_migration_run','reserve_migration_test_checkout','reserve_studio_migration_run',
  'reserve_studio_migration_slot','reserve_subscription_checkout','reserve_subscription_monitoring_site','resolve_migration_match_decisions',
  'schedule_monitor_sweeps','select_studio_run_subscription','set_subscription_monitoring_site_state','start_inventory_discovery',
  'stop_monitoring_sweeps','studio_artifact_entitlement','studio_run_entitlement','subscription_checkout_summary',
  'subscription_site_summary','subscription_work_summary','sync_mcp_queue_failure_state','validate_migration_run_grant',
  'validate_migration_run_inputs','validate_studio_run_authority'
 ]) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
  IF f.proname=ANY(ARRAY[
  'activate_migration_monitor','apply_verified_migration_test_checkout_event','attach_migration_test_checkout','attach_subscription_checkout',
  'attach_subscription_customer','authorize_migration_run_dispatch','checkpoint_inventory_discovery','claim_inventory_discovery',
  'claim_monitor_alert','claim_monitoring_batch','claim_next_job','claim_verification_batch',
  'complete_studio_migration_work','complete_verification_item','consume_gsc_agent_state','create_migration_price_quote',
  'expire_verified_subscription_checkout','finalize_migration_infrastructure_failure','finalize_migration_run_session','finish_gsc_agent_operation',
  'finish_monitor_alert','get_migration_price_quote','get_migration_purchase_grant','get_migration_selection_revision',
  'get_migration_test_checkout','get_migration_test_subscription','get_studio_work_reservation','get_subscription_checkout',
  'get_subscription_monitoring_site','issue_free_migration_grant','list_migration_matches','manage_migration_monitor',
  'match_migration_pages','migration_grant_summary','migration_quote_summary','migration_subscription_summary',
  'migration_test_checkout_summary','monitor_alert_counts','monitor_entitlement','monitor_observed_clicks',
  'monitor_verified_email','persist_migration_run_embedding','persist_migration_run_mapping','plan_migration',
  'publish_artifact_deployment','publish_inventory_import','publish_migration_artifact','reconcile_monitor_sweep',
  'record_migration_grant_success','record_monitoring_item','record_verified_subscription_checkout_period','record_verified_subscription_period',
  'record_verified_test_migration_payment','refresh_migration_gsc_access_token','release_failed_studio_migration_work','reserve_gsc_agent_operation',
  'reserve_included_verification','reserve_migration_operation','reserve_migration_run','reserve_migration_test_checkout',
  'reserve_studio_migration_run','reserve_studio_migration_slot','reserve_subscription_checkout','reserve_subscription_monitoring_site',
  'resolve_migration_match_decisions','schedule_monitor_sweeps','select_studio_run_subscription','set_subscription_monitoring_site_state',
  'start_inventory_discovery','stop_monitoring_sweeps','studio_artifact_entitlement','studio_run_entitlement',
  'subscription_site_summary','subscription_work_summary'
 ]) THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',f.signature);
  END IF;
 END LOOP;
END $hardening$;
COMMIT;
