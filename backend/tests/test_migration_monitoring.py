"""Real PostgreSQL monitoring lifecycle; fake HTTP/Stripe/email boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from backend.services.migration_monitoring_service import MigrationMonitoringService,send_monitoring_alert,run_monitoring_batch,_activation_days
from backend.services.migration_verification_service import VerificationEntitlementError
from backend.services.migration_repository import MigrationRepository,MigrationNotFoundError,InvalidInputError,OperationConflictError
from backend.tests import test_migration_verification as verification_fixture
from backend.tests.test_migration_verification import VerificationClient
from backend.tests.test_migration_planning import A,B

ROOT=Path(__file__).resolve().parents[2]

class PolicyTests(unittest.TestCase):
 def test_activation_policy_is_bounded_and_transparent(self):
  with patch.dict(os.environ,{'MONITORING_ACTIVATION_MAX_DELAY_DAYS':'90'}):self.assertEqual(_activation_days(),90)
  for invalid in ('0','366','bad'):
   with patch.dict(os.environ,{'MONITORING_ACTIVATION_MAX_DELAY_DAYS':invalid}),self.assertRaises(InvalidInputError):_activation_days()

class AlertTemplateTests(unittest.TestCase):
 def test_reentry_context_and_untrusted_evidence_are_escaped(self):
  from jinja2 import Environment,FileSystemLoader
  renderer=Environment(loader=FileSystemLoader(ROOT/'backend/templates/email'),autoescape=True)
  html=renderer.get_template('migration_monitor_alert.html').render(live_origin='<script>bad</script>',total_issues=1,
   issues=[{'source_url':'<img src=x>','expected_url':'https://new.example/a','issue':'wrong_target'}],
   app_url='https://app.example',migration_id=A,monitoring_id=B,sweep_id=A,unsubscribe_url='https://app.example/unsubscribe')
  self.assertNotIn('<script>',html);self.assertNotIn('<img src=x>',html)
  self.assertIn('/migrations/'+A+'?monitoring_id='+B,html)

@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'),'requires disposable local PostgreSQL')
class MonitoringTests(unittest.TestCase):
 cleanup_db=classmethod(verification_fixture.DatabaseTests.cleanup_db.__func__)
 sql=verification_fixture.DatabaseTests.sql
 fixture=verification_fixture.DatabaseTests.fixture
 start_run=verification_fixture.DatabaseTests.start_run
 pay=verification_fixture.DatabaseTests.pay
 claim_run=verification_fixture.DatabaseTests.claim_run
 deployment=verification_fixture.DatabaseTests.deployment

 @classmethod
 def setUpClass(cls):
  verification_fixture.DatabaseTests.setUpClass.__func__(cls)
  import psycopg
  source=Path(os.getenv('MONITORING_SUBSCRIPTION_SQL',str(ROOT/'database/migrations/042_subscription_allowances.sql')))
  with psycopg.connect(cls.dsn,autocommit=True) as conn:
   conn.execute(source.read_text())
   conn.execute((ROOT/'database/migrations/045_recurring_monitoring.sql').read_text())
   conn.execute('ALTER TABLE auth.users ADD COLUMN email text,ADD COLUMN email_confirmed_at timestamptz')
   conn.execute("UPDATE auth.users SET email='verified@example.invalid',email_confirmed_at=now() WHERE id=%s",[A])
  cls.downloads=Mock()
  cls.downloads.authorize_download.side_effect=lambda user,mid,aid:{'resource_type':'artifact_download','artifact_id':aid,'migration_id':mid,'content':'fixture-export-must-not-enter-tool-result'}
  cls.service=MigrationMonitoringService(MigrationRepository(VerificationClient(cls.dsn)),cls.downloads)

 def setUp(self):
  verification_fixture.DatabaseTests.setUp(self)
  self.sql("UPDATE migration_monitors SET state='cancelled',next_check_at=NULL WHERE state<>'cancelled'")
  self.sql("UPDATE migration_monitor_alerts SET state='suppressed' WHERE state IN ('pending','leased')")

 def sub(self,deployment=None,sku='studio',*,subscription=None,status='active',event_at=None,period_start=None,period_end=None):
  now=datetime.now(timezone.utc);ident=subscription or uuid4().hex
  args={'p_user_id':A,'p_subscription_id':'sub_'+ident,'p_customer_id':'cus_'+ident,'p_sku':sku,'p_status':status,
   'p_period_start':(period_start or now-timedelta(hours=1)).isoformat(),'p_period_end':(period_end or now+timedelta(days=29)).isoformat(),
   'p_invoice_id':'in_'+uuid4().hex,'p_amount_cents':9900 if sku=='studio' else 2900,'p_currency':'usd',
   'p_event_id':'evt_'+uuid4().hex,'p_event_hash':'b'*64,'p_event_at':(event_at or now).isoformat(),'p_livemode':False,
   'p_deployment_id':deployment['deployment'] if deployment and sku=='monitoring' else None}
  result=self.service._rpc('record_verified_subscription_period',args)
  return result['subscription_id'],ident

 def begin(self,d,subscription=None,key=None,**kw):
  return self.service.manage(A,d['migration'],'start',artifact_id=d['artifact'],deployment_id=d['deployment'],subscription_id=subscription,idempotency_key=key or uuid4().hex,**kw)

 def manage(self,d,mid,action,key=None):
  return self.service.manage(A,d['migration'],action,monitoring_id=mid,idempotency_key=key or uuid4().hex)

 def finish_sweep(self,states=None):
  import psycopg
  from psycopg.types.json import Jsonb
  self.service.schedule_due(); allitems=[]; sweep=None
  with psycopg.connect(self.dsn) as conn:
   while True:
    batch=self.service.claim('monitor-test',100)
    if not batch:break
    sweep=batch['verification_id'];prepared=[]
    for item in batch['items']:
     state,issue=(states or {}).get(item['ordinal'],('passed',None))
     prepared.append({'ordinal':item['ordinal'],'attempt':item['attempt'],'state':state,
       'finding':{'issue':issue,'measurement':'unavailable' if state=='unchecked' else 'observed'}})
     allitems.append(item['ordinal'])
    rows=conn.execute("""SELECT record_monitoring_item(%s,x.ordinal,'monitor-test',x.attempt,x.state,x.finding)
      FROM jsonb_to_recordset(%s) AS x(ordinal integer,attempt integer,state text,finding jsonb)""",[sweep,Jsonb(prepared)]).fetchall()
    self.assertTrue(all(row[0] for row in rows));conn.commit()
  return sweep,allitems

 def due_again(self,mid):
  self.sql('UPDATE migration_monitors SET next_check_at=now() WHERE id=%s',[mid])

 def test_free_needs_purchase_and_paid_included_anchor_never_resets(self):
  free=self.deployment(3)
  with self.assertRaises(VerificationEntitlementError) as error:self.begin(free)
  self.assertEqual(error.exception.code,'payment_required')
  paid=self.deployment(501);start=self.begin(paid);mid=start['data']['monitoring_id']
  self.assertEqual(start['data']['state'],'active');self.assertEqual(start['data']['source'],'included_paid')
  expires=start['data']['expires_at'];anchor=start['data']['deployment_confirmed_at']
  self.assertEqual(self.manage(paid,mid,'pause')['data']['state'],'paused')
  resumed=self.manage(paid,mid,'resume');self.assertEqual(resumed['data']['expires_at'],expires)
  self.assertEqual(resumed['data']['deployment_confirmed_at'],anchor)
  self.assertEqual(self.manage(paid,mid,'cancel')['data']['state'],'cancelled')
  with self.assertRaises(OperationConflictError):self.manage(paid,mid,'resume')

 def test_studio_and_standalone_accept_free_migration_and_verified_contact_only(self):
  d=self.deployment(3);subscription,_=self.sub()
  with self.assertRaises(InvalidInputError):self.begin(d,subscription,alert_email='stranger@example.invalid')
  start=self.begin(d,subscription,alert_email='verified@example.invalid');mid=start['data']['monitoring_id']
  self.assertEqual(start['data']['state'],'active')
  with self.assertRaises(MigrationNotFoundError):self.service.status(B,d['migration'],mid)
  self.manage(d,mid,'cancel')
  paid_sub,_=self.sub(d,'monitoring')
  self.assertEqual(self.begin(d,paid_sub)['data']['state'],'active')

 def test_awaiting_deployment_and_persisted_schedule_do_not_claim_health(self):
  d=self.deployment(501,installed=False);start=self.begin(d);mid=start['data']['monitoring_id']
  self.assertEqual(start['data']['state'],'awaiting_deployment')
  self.assertEqual(start['data']['coverage']['outcome'],'not_checked')
  self.assertIsNone(start['data']['expires_at']);self.assertEqual(self.service.schedule_due(),0)
  self.manage(d,mid,'pause');self.assertEqual(self.manage(d,mid,'resume')['data']['state'],'awaiting_deployment')
  self.sql("UPDATE artifact_deployments SET status='installation_reported',installation_reported_at=now() WHERE id=%s",[d['deployment']])
  self.assertEqual(self.service.schedule_due(),1)
  active=self.service.status(A,d['migration'],mid)
  self.assertEqual(active['data']['state'],'active');self.assertEqual(active['data']['coverage']['unchecked'],501)
  self.assertFalse(active['data']['coverage']['complete'])

 def test_partial_sweeps_never_healthy_and_transient_alerts_require_repeat(self):
  d=self.deployment(3);subscription,_=self.sub();mid=self.begin(d,subscription)['data']['monitoring_id']
  states={2:('unchecked','timeout')}
  sweep,_=self.finish_sweep(states)
  status=self.service.status(A,d['migration'],mid)
  self.assertEqual(status['data']['coverage']['outcome'],'unverifiable')
  self.assertIsNone(status['data']['last_complete_sweep_at']);self.assertEqual(status['data']['alerts']['pending'],0)
  fixes=self.service.fixes(A,d['migration'],mid);self.assertIsNone(fixes['data']['recovery_artifact'])
  self.assertEqual(fixes['next_action'],'retry')
  self.due_again(mid);self.finish_sweep(states)
  self.assertEqual(self.service.status(A,d['migration'],mid)['data']['alerts']['pending'],1)
  self.due_again(mid);self.finish_sweep(states)
  self.assertEqual(self.service.status(A,d['migration'],mid)['data']['alerts']['pending'],1)
  self.due_again(mid);self.finish_sweep()
  after=self.service.status(A,d['migration'],mid)
  self.assertEqual(after['data']['coverage']['outcome'],'passed')
  self.assertIsNotNone(after['data']['last_complete_sweep_at'])
  self.assertEqual(self.sql("SELECT state FROM migration_monitor_issues WHERE monitoring_id=%s",[mid])[0]['state'],'resolved')

 def test_pause_cancels_unissued_work_stale_results_and_resume_keeps_expiry(self):
  d=self.deployment();sub,_=self.sub();start=self.begin(d,sub);mid=start['data']['monitoring_id'];expiry=start['data']['expires_at']
  self.service.schedule_due();batch=self.service.claim('stale',3)
  self.manage(d,mid,'pause')
  self.assertFalse(self.service.record(batch['verification_id'],batch['items'][0],'stale','passed',{}))
  self.assertIsNone(self.service.claim('blocked',3))
  resumed=self.manage(d,mid,'resume');self.assertEqual(resumed['data']['expires_at'],expiry)
  sweep,rows=self.finish_sweep();self.assertEqual(len(rows),3)
  self.assertNotEqual(sweep,batch['verification_id'])

 def test_subscription_lapse_stops_per_batch_and_does_not_start_indefinite_free_work(self):
  d=self.deployment();sub,ident=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  self.service.schedule_due();batch=self.service.claim('worker',1)
  self.sub(subscription=ident,status='past_due',event_at=datetime.now(timezone.utc)+timedelta(seconds=1))
  self.assertFalse(self.service.record(batch['verification_id'],batch['items'][0],'worker','passed',{}))
  self.assertIsNone(self.service.claim('worker',2))
  status=self.service.status(A,d['migration'],mid)
  self.assertEqual(status['data']['state'],'expired');self.assertEqual(status['data']['coverage']['checked'],0)
  with self.assertRaises(VerificationEntitlementError):self.manage(d,mid,'resume')

 def test_full_scope_failed_tail_has_downloadable_expected_artifact_and_requires_recheck(self):
  d=self.deployment(15000);sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  sweep,rows=self.finish_sweep({14999:('failed','wrong_target')})
  self.assertEqual(sorted(rows),list(range(15000)))
  status=self.service.status(A,d['migration'],mid)
  self.assertTrue(status['data']['coverage']['complete']);self.assertEqual(status['data']['coverage']['failed'],1)
  fixes=self.service.fixes(A,d['migration'],mid)
  self.assertEqual(fixes['data']['recovery_artifact']['artifact_id'],d['artifact'])
  self.assertNotIn('content',fixes['data']['recovery_artifact'])
  self.assertEqual(fixes['data']['items'][0]['state'],'open')
  self.assertTrue(fixes['data']['items'][0]['requires_verification'])
  self.downloads.authorize_download.assert_called_with(A,d['migration'],d['artifact'])
  self.assertEqual(self.sql("SELECT state FROM migration_monitor_issues WHERE monitoring_id=%s",[mid])[0]['state'],'open')

 def test_alert_retry_uses_stable_provider_key_positive_receipt_and_no_duplicate_sweep_alert(self):
  d=self.deployment();sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  self.finish_sweep({1:('failed','wrong_target')})
  self.sql('UPDATE migration_monitor_alerts SET available_at=now() WHERE monitoring_id=%s',[mid])
  sender=Mock(side_effect=[None,'message-fixture'])
  first=send_monitoring_alert(self.service,'mailer',sender=sender);self.assertFalse(first['delivered'])
  self.sql('UPDATE migration_monitor_alerts SET available_at=now() WHERE monitoring_id=%s',[mid])
  second=send_monitoring_alert(self.service,'mailer',sender=sender);self.assertTrue(second['delivered'])
  self.assertEqual(sender.call_args_list[0],sender.call_args_list[1])
  self.due_again(mid);self.finish_sweep({1:('failed','wrong_target')})
  self.assertFalse(send_monitoring_alert(self.service,'mailer',sender=sender)['claimed'])
  self.assertEqual(sender.call_count,2)

 def test_new_state_idempotency_and_concurrent_schedule_make_one_sweep(self):
  d=self.deployment();sub,_=self.sub();start=self.begin(d,sub,key='stable');mid=start['data']['monitoring_id']
  self.assertEqual(self.begin(d,sub,key='stable')['data']['monitoring_id'],mid)
  with self.assertRaises(OperationConflictError):self.begin(d,key='stable')
  with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(lambda _:self.service.schedule_due(),range(5)))
  self.assertEqual(sum(results),1)
  self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_verifications WHERE monitoring_id=%s',[mid])[0]['n'],1)

 def test_verified_paid_renewal_allows_explicit_resume_without_resetting_deployment(self):
  import time
  d=self.deployment();end=datetime.now(timezone.utc)+timedelta(seconds=2)
  sub,ident=self.sub(period_end=end);start=self.begin(d,sub);mid=start['data']['monitoring_id']
  time.sleep(2.05)
  self.assertEqual(self.service.schedule_due(),0)
  self.assertEqual(self.service.status(A,d['migration'],mid)['data']['state'],'expired')
  with self.assertRaises(VerificationEntitlementError):self.manage(d,mid,'resume')
  self.sub(subscription=ident,period_start=end,period_end=end+timedelta(days=30))
  resumed=self.manage(d,mid,'resume')
  self.assertEqual(resumed['data']['state'],'active')
  self.assertEqual(resumed['data']['deployment_confirmed_at'],start['data']['deployment_confirmed_at'])
  self.assertNotEqual(resumed['data']['expires_at'],start['data']['expires_at'])

 def test_advance_paid_period_preserves_current_monitoring_authority(self):
  d=self.deployment();end=datetime.now(timezone.utc)+timedelta(days=10)
  sub,ident=self.sub(period_end=end)
  self.sub(subscription=ident,period_start=end,period_end=end+timedelta(days=30))
  pointer=self.sql('SELECT p.period_start FROM migration_test_subscriptions s JOIN migration_test_subscription_periods p ON p.id=s.current_period_id WHERE s.id=%s',[sub])[0]
  self.assertEqual(pointer['period_start'],end)
  start=self.begin(d,sub);mid=start['data']['monitoring_id']
  self.assertEqual(start['data']['state'],'active')
  self.assertEqual(datetime.fromisoformat(start['data']['expires_at']),end)
  sweep,items=self.finish_sweep()
  self.assertTrue(sweep);self.assertEqual(len(items),3)
  self.assertEqual(self.service.status(A,d['migration'],mid)['data']['state'],'active')
  self.manage(d,mid,'pause')
  self.assertEqual(self.manage(d,mid,'resume')['data']['state'],'active')

 def test_included_check_queue_remains_separate_from_recurring_sweeps(self):
  from backend.services.migration_verification_service import MigrationVerificationService
  d=self.deployment();sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  self.service.schedule_due()
  included=MigrationVerificationService(self.service.repository)
  start=included.start(A,d['migration'],d['artifact'],d['deployment'],'one-shot')
  single=included.claim('included-worker',100)
  self.assertEqual(single['verification_id'],start['data']['verification_id'])
  recurring=self.service.claim('recurring-worker',100)
  self.assertNotEqual(single['verification_id'],recurring['verification_id'])
  self.assertEqual(len(single['items']),len(recurring['items']))

 def test_observed_traffic_prioritizes_without_dropping_unmeasured_tail(self):
  d=self.deployment();sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  self.sql('CREATE TABLE IF NOT EXISTS gsc_migration_metrics(migration_id uuid,url text,clicks bigint)')
  self.sql('INSERT INTO gsc_migration_metrics VALUES(%s,%s,100)',[d['migration'],'https://old.example/Page/2?q=A'])
  self.service.schedule_due();head=self.service.claim('priority',1)
  self.assertEqual(head['items'][0]['ordinal'],2)
  tail=self.service.claim('tail',100)
  self.assertEqual([item['ordinal'] for item in tail['items']],[0,1])
  self.assertTrue(all(item['priority_clicks'] is None for item in tail['items']))

 def test_included_clock_expiry_and_late_activation_are_actionable(self):
  import psycopg
  d=self.deployment(501,installed=False)
  # Synthetic persisted historical clocks exercise worker expiry without
  # mutating an existing immutable anchor or waiting90 days.
  with psycopg.connect(self.dsn) as conn:
   mid=conn.execute("""INSERT INTO migration_monitors(user_id,migration_id,artifact_id,deployment_id,live_origin,state,included_grant_id,activation_deadline)
    SELECT user_id,migration_id,artifact_id,id,live_origin,'awaiting_deployment',%s,now()-interval '1 day' FROM artifact_deployments WHERE id=%s RETURNING id""",[d['grant'],d['deployment']]).fetchone()[0]
  self.service.schedule_due();late=self.service.status(A,d['migration'],str(mid))
  self.assertEqual(late['data']['state'],'expired');self.assertEqual(late['data']['state_reason'],'activation_late')
  second=self.deployment(501)
  with psycopg.connect(self.dsn) as conn:
   old=conn.execute("""INSERT INTO migration_monitors(user_id,migration_id,artifact_id,deployment_id,live_origin,state,included_grant_id,
     deployment_confirmed_at,expires_at,activation_deadline,next_check_at)
    SELECT user_id,migration_id,artifact_id,id,live_origin,'active',%s,now()-interval '31 days',now()-interval '1 day',now()-interval '2 days',now()
     FROM artifact_deployments WHERE id=%s RETURNING id""",[second['grant'],second['deployment']]).fetchone()[0]
  self.assertEqual(self.service.schedule_due(),0)
  expired=self.service.status(A,second['migration'],str(old))
  self.assertEqual(expired['data']['state'],'expired');self.assertEqual(expired['next_action'],'complete_payment')
  with self.assertRaises(VerificationEntitlementError):self.manage(second,str(old),'resume')

 def test_ambiguous_old_alert_is_not_resent_and_unverified_contact_is_suppressed(self):
  d=self.deployment();sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id']
  self.finish_sweep({1:('failed','wrong_target')})
  self.sql("UPDATE migration_monitor_alerts SET available_at=now(),first_attempt_at=now()-interval '24 hours' WHERE monitoring_id=%s",[mid])
  sender=Mock(return_value='message-never')
  self.assertFalse(send_monitoring_alert(self.service,'mailer',sender=sender)['claimed']);sender.assert_not_called()
  self.assertEqual(self.service.status(A,d['migration'],mid)['data']['alerts']['delivery_uncertain'],1)
  self.due_again(mid);self.finish_sweep({2:('failed','not_found')})
  self.sql('UPDATE migration_monitor_alerts SET available_at=now() WHERE monitoring_id=%s',[mid])
  self.sql('UPDATE auth.users SET email_confirmed_at=NULL WHERE id=%s',[A])
  try:self.assertFalse(send_monitoring_alert(self.service,'mailer',sender=sender)['claimed'])
  finally:self.sql('UPDATE auth.users SET email_confirmed_at=now() WHERE id=%s',[A])
  sender.assert_not_called()

 def test_account_cleanup_and_roles(self):
  import psycopg
  d=self.deployment();sub,_=self.sub();mid=self.begin(d,sub)['data']['monitoring_id'];self.finish_sweep({1:('failed','not_found')})
  for role in ('anon','authenticated'):
   with psycopg.connect(self.dsn) as conn:
    conn.execute('SET ROLE '+role)
    with self.assertRaises(psycopg.errors.InsufficientPrivilege):conn.execute("SELECT schedule_monitor_sweeps(1)")
  with psycopg.connect(self.dsn) as conn:
   conn.execute('DELETE FROM auth.users WHERE id=%s',[A])
   self.assertEqual(conn.execute('SELECT count(*) FROM migration_monitors WHERE id=%s',[mid]).fetchone()[0],0)
   self.assertEqual(conn.execute('SELECT count(*) FROM migration_monitor_alerts WHERE monitoring_id=%s',[mid]).fetchone()[0],0)
   conn.rollback()
