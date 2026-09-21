"""Durable optional monitoring of a fixed deployed artifact, not latest matches."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from .migration_repository import InvalidInputError, MigrationNotFoundError, _strict_uuid
from .migration_planning_service import envelope, validate_key
from .migration_verification_service import MigrationVerificationService, run_verification_batch


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z') if isinstance(value,datetime) else value


def _activation_days():
    try:
        value=int(os.environ.get('MONITORING_ACTIVATION_MAX_DELAY_DAYS','90'))
        if not 1<=value<=365: raise ValueError()
    except ValueError:
        raise InvalidInputError('Monitoring activation delay must be configured between 1 and 365 days.') from None
    return value


class MigrationMonitoringService(MigrationVerificationService):
    def __init__(self, repository=None, artifact_service=None):
        super().__init__(repository)
        self.artifact_service=artifact_service

    def manage(self,user_id,migration_id,action,*,artifact_id=None,deployment_id=None,monitoring_id=None,subscription_id=None,alert_email=None,idempotency_key=None):
        _,user=_strict_uuid(user_id,'user_id'); _,migration=_strict_uuid(migration_id,'migration_id')
        if action not in ('start','pause','resume','cancel'):
            raise InvalidInputError('Unsupported monitoring action.')
        ids={}
        for name,value in [('artifact',artifact_id),('deployment',deployment_id),('monitor',monitoring_id),('subscription',subscription_id)]:
            ids['p_'+name]=_strict_uuid(value,name+'_id')[1] if value is not None else None
        if action=='start':
            if not artifact_id or not deployment_id or monitoring_id is not None:
                raise InvalidInputError('Start requires artifact_id and deployment_id.')
        elif not monitoring_id or any(v is not None for v in (artifact_id,deployment_id,subscription_id,alert_email)):
            raise InvalidInputError('Pause, resume and cancel require only the existing monitoring_id.')
        if alert_email is not None and (not isinstance(alert_email,str) or len(alert_email)>254 or any(ord(c)<32 for c in alert_email)):
            raise InvalidInputError('Use the account’s verified email address for alerts.')
        row=self._rpc('manage_migration_monitor',{'p_user':user,'p_migration':migration,'p_action':action,**ids,
            'p_alert_email':alert_email,'p_key':validate_key(idempotency_key),'p_activation_days':_activation_days()})
        return self.status(user,migration,str(row['id']))

    def _monitor(self,user,migration,monitor):
        _,user=_strict_uuid(user,'user_id'); _,migration=_strict_uuid(migration,'migration_id'); _,monitor=_strict_uuid(monitor,'monitoring_id')
        rows=self.repository._execute(self.repository.client.table('migration_monitors').select('*').eq('user_id',user).eq('migration_id',migration).eq('id',monitor)).data
        if not rows: raise MigrationNotFoundError('Monitoring site not found.')
        return rows[0]

    def status(self,user_id,migration_id,monitoring_id):
        row=self._monitor(user_id,migration_id,monitoring_id)
        entitlement=self._rpc('monitor_entitlement',{'p_monitor':str(row['id'])})
        state=row['state']
        if state=='active' and not entitlement['eligible']: state='expired'
        coverage={'outcome':'not_checked','total':None,'checked':0,'failed':None,'unchecked':None,'complete':False}
        if row['last_sweep_id']:
            rows=self.repository._execute(self.repository.client.table('migration_verifications').select('*').eq('id',str(row['last_sweep_id']))).data
            if rows: coverage=MigrationVerificationService._envelope(rows[0])['data']
        counts=self._rpc('monitor_alert_counts',{'p_monitor':str(row['id'])})
        counts['contact_verified']=bool(self._rpc('monitor_verified_email',{'p_user':str(row['user_id'])}))
        action='install_artifact' if state=='awaiting_deployment' else 'complete_payment' if state=='expired' else 'none'
        return envelope(str(row['migration_id']),status='needs_input' if state in ('awaiting_deployment','expired') else 'succeeded',next_action=action,data={
            'monitoring_id':str(row['id']),'artifact_id':str(row['artifact_id']),'deployment_id':str(row['deployment_id']),
            'live_origin':row['live_origin'],'state':state,'state_reason':row.get('state_reason'),
            'source':'included_paid' if row['included_grant_id'] else 'subscription',
            'deployment_confirmed_at':_iso(row['deployment_confirmed_at']),
            'expires_at':_iso(row['expires_at'] if row['included_grant_id'] else entitlement.get('expires_at')),
            'activation_deadline':_iso(row['activation_deadline']),'cadence_hours':row['cadence_hours'],
            'next_check_at':_iso(row['next_check_at']) if state=='active' else None,
            'last_complete_sweep_at':_iso(row['last_complete_sweep_at']),
            'coverage':coverage,'alerts':counts,'renewal':'explicit_purchase_required' if row['included_grant_id'] else 'verified_subscription_periods_only',
            'summary':'Scheduled artifact checks; coverage and freshness describe measured results. Starting monitoring does not purchase renewal.'})

    def fixes(self,user_id,migration_id,monitoring_id,*,after=-1,limit=100):
        row=self._monitor(user_id,migration_id,monitoring_id)
        if isinstance(after,bool) or not isinstance(after,int) or after < -1 or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=500:
            raise InvalidInputError('Use an ordinal cursor and a limit between 1 and 500.')
        issues=self.repository._execute(self.repository.client.table('migration_monitor_issues').select('*').eq('monitoring_id',str(row['id']))
            .in_('state',['open']).gt('ordinal',after).order('ordinal').limit(limit+1)).data
        actionable=[item for item in issues[:limit] if item['evidence'].get('measurement')!='unavailable']
        artifact=None
        # A recovery download is the existing immutable expected artifact. It
        # cannot create a new decision revision or mark a deployed rule fixed.
        if actionable:
            service=self.artifact_service
            if service is None:
                from .migration_artifact_service import MigrationArtifactService
                service=MigrationArtifactService(self.repository)
            authorized=service.authorize_download(user_id,migration_id,str(row['artifact_id']))
            artifact={key:authorized[key] for key in ('resource_type','artifact_id','migration_id','content_hash','format') if key in authorized}
        return envelope(str(row['migration_id']),status='succeeded',next_action='install_artifact' if actionable else 'retry' if issues else 'none',data={
            'monitoring_id':str(row['id']),'recovery_artifact':artifact,
            'items':[{'issue_id':str(item['id']),'mapping_id':item['mapping_id'],'source_url':item['source_url'],
                'expected_url':item['expected_url'],'issue':item['issue'],'evidence':item['evidence'],
                'first_seen_at':_iso(item['first_seen_at']),'last_seen_at':_iso(item['last_seen_at']),
                'state':'open','requires_verification':True,'suggested_action':'retry_check' if item['evidence'].get('measurement')=='unavailable' else 'restore_artifact_rule'} for item in issues[:limit]],
            'next_cursor':issues[limit-1]['ordinal'] if len(issues)>limit else None,
            'summary':'Restore the expected artifact rules, then verify. Unmeasured URLs and generated fixes are not proof of resolution.'})

    def schedule_due(self,limit=20):
        return self._rpc('schedule_monitor_sweeps',{'p_limit':limit})

    def claim(self,worker_id,batch_size=50):
        if not isinstance(worker_id,str) or not worker_id.strip() or len(worker_id)>100 or isinstance(batch_size,bool) or not isinstance(batch_size,int) or not 1<=batch_size<=100:
            raise InvalidInputError('Worker ID and batch size 1–100 are required.')
        return self._rpc('claim_monitoring_batch',{'p_worker':worker_id,'p_limit':batch_size})

    def record(self,verification_id,item,worker_id,state,finding):
        return self._rpc('record_monitoring_item',{'p_verification':verification_id,'p_ordinal':item['ordinal'],
            'p_worker':worker_id,'p_attempt':item['attempt'],'p_state':state,'p_finding':finding})


async def run_monitoring_batch(service,worker_id,batch_size=50):
    scheduled=await asyncio.to_thread(service.schedule_due)
    result=await run_verification_batch(service,worker_id,batch_size)
    return {'scheduled':scheduled,**result}


def send_monitoring_alert(service,worker_id,*,sender=None):
    """One durable alert attempt. Tests inject a sender and never send real mail."""
    alert=service._rpc('claim_monitor_alert',{'p_worker':worker_id})
    if not alert: return {'claimed':False}
    suppressed=False
    try:
        result=(sender or _send_alert)(alert['payload'],'monitor-alert/'+str(alert['id']))
        suppressed=result=='suppressed'
        message_id=result if not suppressed and isinstance(result,str) and result.strip() and len(result)<=256 else None
    except Exception:
        message_id=None
    recorded=service._rpc('finish_monitor_alert',{'p_id':str(alert['id']),'p_worker':worker_id,'p_attempt':alert['attempt'],
        'p_message_id':message_id,'p_suppressed':suppressed})
    return {'claimed':True,'delivered':bool(message_id) and bool(recorded),'suppressed':suppressed and bool(recorded)}


def _send_alert(payload,idempotency_key):
    # Existing preference and template boundaries, with provider-level replay
    # protection for the durable outbox. No email credentials enter tool output.
    import resend
    from src.redirx.config import Config
    from .email_service import EmailService, EmailType
    service=EmailService()
    if service._is_opted_out(payload['user_id'],EmailType.WATCH_ALERT): return 'suppressed'
    if not Config.RESEND_API_KEY: return None
    unsubscribe=service._unsubscribe_url(payload['user_id'],EmailType.WATCH_ALERT)
    html=service._jinja.get_template('migration_monitor_alert.html').render(
        **payload,app_url=service._app_url,unsubscribe_url=unsubscribe)
    result=resend.Emails.send({'from':service._from_address,'to':[payload['to']],
        'subject':'Redirect monitoring findings for '+payload['live_origin'],'html':html,
        'headers':{'List-Unsubscribe':'<'+unsubscribe+'>'}}, {'idempotency_key':idempotency_key})
    return result.get('id') if isinstance(result,dict) else getattr(result,'id',None)
