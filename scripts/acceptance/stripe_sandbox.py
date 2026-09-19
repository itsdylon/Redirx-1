"""Interactive TEST Stripe acceptance. No provider calls until POST /control/create.

Run with stripe_sandbox.mjs, which owns a fresh local PostgreSQL cluster.
No app .env is loaded. Secrets remain in memory; output is an atomic0600 state file.
"""
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flask import Flask, jsonify, request
import stripe
from werkzeug.serving import make_server, WSGIRequestHandler
from backend.tests.test_studio_run_http import StudioRunHTTPAcceptance
from backend.tests.helpers.pivot_native_client import NativeClient, json_value
from backend.services.migration_repository import MigrationRepository, MigrationRepositoryError
from backend.services.migration_quote_service import MigrationQuoteService
from backend.services.migration_run_service import MigrationRunService
from backend.services.migration_checkout_service import MigrationCheckoutService
from backend.services.migration_subscription_checkout_service import MigrationSubscriptionCheckoutService
from backend.services.migration_subscription_service import MigrationSubscriptionService
from backend.services.migration_artifact_service import MigrationArtifactService


class QuietHTTP(WSGIRequestHandler):
    def log_request(self, *args): pass


def read_secret(variable, prefix):
    path = Path(os.environ[variable])
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f'{variable} must point to a private regular file.')
    value = path.read_text().strip()
    if not value.startswith(prefix) or len(value) <= len(prefix):
        raise ValueError(f'{variable} has the wrong credential type.')
    return value


class Harness:
    def __init__(self, port, state_file):
        self.origin = f'http://127.0.0.1:{port}'
        self.state_file = Path(state_file)
        self.lock = threading.RLock()
        self.receipts = []
        self.checkouts = {}
        self.errors = []
        self.rights_results = {}
        self.services = None
        StudioRunHTTPAcceptance.setUpClass()
        self.fixture = StudioRunHTTPAcceptance('runTest')
        import psycopg
        with psycopg.connect(self.fixture.dsn, autocommit=True) as conn:
            conn.execute('CREATE TABLE webpage_embeddings (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES migration_sessions(id), url text, site_type text, embedding jsonb, extracted_text text, title text)')
            for number in (50, 51):
                for path in sorted((ROOT/'database/migrations').glob(f'{number:03}_*.sql')):
                    conn.execute(path.read_text())
        self.native = NativeClient(self.fixture.dsn)
        self.repo = MigrationRepository(self.native)
        self.quotes = MigrationQuoteService(self.repo)
        self.runs = MigrationRunService(self.repo)
        self.fixture.repo, self.fixture.quotes = self.repo, self.quotes
        self.fixture.owner = str(uuid4())
        self.owner = self.fixture.owner
        self.fixture.sql('INSERT INTO auth.users(id) VALUES (%s)', [self.owner])
        self.fixture.sql('INSERT INTO user_profiles(id) VALUES (%s)', [self.owner])
        self.paid = self.fixture.fixture(501)
        self.pending = self.runs.start_run(self.owner, self.paid['migration'], self.paid['old'], self.paid['new'],
            self.paid['quote'], 'sandbox-paid-run')
        assert self.pending['status'] == 'payment_required' and self.pending['run_id'] is None
        self.studio_work = self.fixture.fixture(501)
        self.deployment = self.seed_deployment()
        self.initial_counts = self.counts()
        self.write_state()

    def seed_deployment(self):
        """Controlled mapping fixture, real worker lease/persist/export/install SQL.

        This records an installation report for example.test; it does not assert
        public deployment, perform fetches, or represent matching-engine acceptance.
        """
        f = self.fixture.fixture(1)
        run = self.runs.start_run(self.owner, f['migration'], f['old'], f['new'], f['quote'], 'sandbox-install-run')
        rows = self.fixture.sql("SELECT * FROM claim_next_job('sandbox-fixture',now()+interval '10 minutes')")
        assert len(rows) == 1
        job = json_value(rows[0]); self.runs.authorize_dispatch(job, 'sandbox-fixture')
        self.native.rpc('persist_migration_run_mapping', {
            'p_session_id': job['id'], 'p_run_id': run['run_id'], 'p_worker_id': 'sandbox-fixture',
            'p_attempt_count': job['attempt_count'], 'p_old_url': job['old_urls'][0], 'p_new_url': job['new_urls'][0],
            'p_confidence_score': 1.0, 'p_match_type': 'exact_url', 'p_needs_review': False,
        }).execute()
        self.runs.finalize_session(job, 'sandbox-fixture', 'completed')
        revision = self.native.rpc('get_migration_selection_revision', {'p_user_id': self.owner,
            'p_migration_id': f['migration'], 'p_run_id': run['run_id']}).execute().data['selection_revision']
        artifacts = MigrationArtifactService(self.repo)
        artifact = artifacts.create_artifact(self.owner, f['migration'], run['run_id'], idempotency_key='sandbox-artifact',
            fmt='json', selection_revision=str(revision))
        deployed = artifacts.report_installation(self.owner, f['migration'], artifact['id'], 'https://old.example',
            idempotency_key='sandbox-installation', installation_report={'fixture': 'sandbox billing acceptance; no live deployment claimed'})
        return {**deployed, 'artifact_id': artifact['id'], 'migration_id': f['migration']}

    def configure(self):
        if self.services is None:
            key = read_secret('STRIPE_ACCEPTANCE_KEY_FILE', 'sk_test_')
            secret = read_secret('STRIPE_ACCEPTANCE_WEBHOOK_SECRET_FILE', 'whsec_')
            common = dict(repository=self.repo, secret_key=key, webhook_secret=secret, companion_origin=self.origin)
            self.services = (MigrationCheckoutService(**common), MigrationSubscriptionCheckoutService(**common))
        return self.services

    def counts(self):
        tables = {'paid_grants': ('migration_purchase_grants', "user_id=%s AND source='stripe_test'"),
                  'subscriptions': ('migration_test_subscriptions', 'user_id=%s'),
                  'paid_periods': ('migration_test_subscription_periods', 'user_id=%s'),
                  'studio_slots': ('migration_studio_slots', 'user_id=%s'),
                  'monitoring_slots': ('migration_subscription_site_slots', 'user_id=%s')}
        return {label: self.fixture.sql(f'SELECT count(*) AS n FROM {table} WHERE {where}', [self.owner])[0]['n']
                for label, (table, where) in tables.items()}

    def state(self):
        states = {}
        if self.services:
            payment, recurring = self.services
            for label, checkout in self.checkouts.items():
                states[label] = payment.get_checkout(self.owner, self.paid['migration'], checkout['checkout_id']) if label == 'migration' \
                    else recurring.get_checkout(self.owner, checkout['checkout_id'])
        return {'test_only': True, 'receiver': self.origin+'/webhooks/stripe', 'owner_id': self.owner,
                'phase': 'checkouts_created' if self.checkouts else 'awaiting_explicit_create',
                'paid_quote_id': self.paid['quote'], 'pending_operation_id': self.pending['operation_id'],
                'deployment_id': self.deployment['deployment_id'], 'checkouts': states,
                'initial_counts': self.initial_counts if hasattr(self, 'initial_counts') else self.counts(),
                'counts': self.counts(), 'verified_receipts': self.receipts, 'rights_results': self.rights_results, 'errors': self.errors}

    def write_state(self):
        value = self.state()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_name(self.state_file.name+'.tmp')
        fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output: json.dump(json_value(value), output, indent=2)
        os.replace(temp, self.state_file)
        return value

    def create(self, labels):
        payment, recurring = self.configure()
        for label in labels:
            if label == 'migration':
                result = payment.create_checkout(self.owner, self.paid['migration'], self.paid['quote'],
                    self.pending['operation_id'], 'sandbox-oneoff-checkout')
            elif label in ('studio', 'monitoring'):
                result = recurring.create_checkout(self.owner, label, 'sandbox-'+label, recurring_consent=True,
                    deployment_id=self.deployment['deployment_id'] if label == 'monitoring' else None)
            else: raise ValueError('Unknown acceptance scenario.')
            self.checkouts[label] = result
            self.write_state()
        # Browser success query assertions must never affect persisted authority.
        before = self.counts()
        for label, checkout in self.checkouts.items():
            (payment.get_checkout(self.owner, self.paid['migration'], checkout['checkout_id']) if label == 'migration'
             else recurring.get_checkout(self.owner, checkout['checkout_id']))
        assert before == self.counts()
        return self.write_state()

    def webhook(self, raw, signature):
        payment, recurring = self.configure()
        # Signature is verified before inspecting routing metadata. The shipped
        # service independently repeats verification and retrieves provider facts.
        event = stripe.Webhook.construct_event(raw, signature, payment.webhook_secret, tolerance=300)
        if event.get('livemode') is not False or event.get('account') is not None:
            raise ValueError('Only direct sandbox events are accepted.')
        obj = event['data']['object']
        if event['type'].startswith('checkout.session.'):
            service = payment if obj.get('mode') == 'payment' else recurring
        elif event['type'] == 'charge.refunded':
            service = payment if obj.get('metadata', {}).get('redirx_checkout_id') else recurring
        else: service = recurring
        before = self.counts()
        first = service.handle_webhook(raw, signature)
        after = self.counts()
        second = service.handle_webhook(raw, signature)
        replay = self.counts()
        assert replay == after, 'Verified event replay changed authority counts.'
        receipt = {'event_id': event['id'], 'type': event['type'], 'verified_by_shipped_service': True,
                   'ignored': bool(first.get('ignored')), 'before': before, 'after': after,
                   'immediate_signed_replay_unchanged': replay == after,
                   'result': first, 'replay_result': second}
        self.receipts.append(receipt); self.write_state()
        return {'received': True, 'replay_verified': True}

    def exercise_rights(self):
        """Only grants already written by real verified webhooks may be exercised."""
        subscriptions = MigrationSubscriptionService(self.repo)
        results = {}
        if self.counts()['paid_grants']:
            first = self.runs.start_run(self.owner, self.paid['migration'], self.paid['old'], self.paid['new'],
                self.paid['quote'], 'sandbox-paid-run')
            second = self.runs.start_run(self.owner, self.paid['migration'], self.paid['old'], self.paid['new'],
                self.paid['quote'], 'sandbox-paid-run')
            assert first['operation_id'] == self.pending['operation_id'] and first['run_id'] == second['run_id']
            results['migration'] = {'operation_resumed': True, 'run_id': first['run_id']}
        for label in ('studio', 'monitoring'):
            checkout = self.checkouts.get(label)
            if not checkout: continue
            stored = self.services[1].get_checkout(self.owner, checkout['checkout_id'])
            sub = stored.get('subscription_id')
            if not sub: continue
            if label == 'studio':
                f = self.studio_work
                first = subscriptions.start_studio_run(self.owner, sub, f['migration'], f['old'], f['new'], f['quote'], 'sandbox-studio-work')
                second = subscriptions.start_studio_run(self.owner, sub, f['migration'], f['old'], f['new'], f['quote'], 'sandbox-studio-work')
                assert first['run_id'] == second['run_id']
                results[label] = {'run_id': first['run_id'], 'exact_retry': True}
            else:
                first = subscriptions.reserve_monitoring_site(self.owner, sub, self.deployment['deployment_id'], 'sandbox-monitoring-site')
                second = subscriptions.reserve_monitoring_site(self.owner, sub, self.deployment['deployment_id'], 'sandbox-monitoring-site')
                assert first['slot_id'] == second['slot_id']
                results[label] = {'slot_id': first['slot_id'], 'exact_retry': True}
        self.rights_results = results
        self.write_state()
        return {'results': results, 'counts': self.counts()}


def main():
    os.environ.update(MCP_PIVOT_ENABLED='true', MCP_PIVOT_ACTIVATION='test_only')
    port = int(os.getenv('STRIPE_ACCEPTANCE_PORT', '55441'))
    harness = Harness(port, os.getenv('STRIPE_ACCEPTANCE_STATE_FILE', '/private/tmp/redirx-operation-20260919/stripe-acceptance-state.json'))
    app = Flask('stripe-sandbox-acceptance'); app.config['MAX_CONTENT_LENGTH'] = 1024*1024

    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1', '::1'): return jsonify(error='loopback_only'), 403
        if request.path.startswith('/control/') and (request.method != 'POST' or not request.is_json
                or request.headers.get('Origin') not in (None, harness.origin)):
            return jsonify(error='explicit_local_json_required'), 403

    @app.get('/state')
    @app.get('/migrations/<migration>')
    @app.get('/billing/subscriptions/return')
    def status(migration=None):
        with harness.lock:
            before = harness.counts(); state = harness.write_state()
            assert harness.counts() == before  # all return query parameters are inert
            return jsonify(state)

    @app.post('/control/create')
    def create():
        value = request.get_json()
        if value.get('confirm_test_mode') is not True or value.get('recurring_consent') is not True:
            return jsonify(error='explicit_test_mode_and_recurring_consent_required'), 400
        labels = value.get('scenarios', ['migration', 'studio', 'monitoring'])
        if not isinstance(labels, list) or not labels or any(x not in ('migration','studio','monitoring') for x in labels):
            return jsonify(error='invalid_scenarios'), 400
        with harness.lock: return jsonify(harness.create(labels))

    @app.post('/control/exercise-rights')
    def exercise():
        with harness.lock: return jsonify(harness.exercise_rights())

    @app.post('/control/assert-complete')
    def assert_complete():
        with harness.lock:
            state = harness.write_state()
            expected = {'paid_grants': 1, 'subscriptions': 2, 'paid_periods': 2, 'studio_slots': 1, 'monitoring_slots': 1}
            checks = {
                'exact_authority_counts': state['counts'] == expected,
                'all_rights_exercised': set(harness.rights_results) == {'migration','studio','monitoring'},
                'all_checkout_states_verified': all(state['checkouts'].get(label, {}).get('status' if label == 'migration' else 'state')
                    == ('paid' if label == 'migration' else 'complete') for label in ('migration','studio','monitoring')),
                'real_signed_events_received': len([r for r in harness.receipts if not r['ignored']]) >= 3,
                'all_immediate_replays_unchanged': bool(harness.receipts) and all(r['immediate_signed_replay_unchanged'] for r in harness.receipts),
            }
            return jsonify(accepted=all(checks.values()), checks=checks), 200 if all(checks.values()) else 409

    @app.post('/control/shutdown')
    def shutdown():
        threading.Thread(target=server.shutdown, daemon=True).start()
        return jsonify(stopping=True)

    @app.post('/webhooks/stripe')
    def webhook():
        with harness.lock:
            return jsonify(harness.webhook(request.get_data(), request.headers.get('Stripe-Signature', '')))

    @app.errorhandler(Exception)
    def failed(exc):
        # Never serialize SDK messages, request bodies, headers, or secrets.
        code = exc.code if isinstance(exc, MigrationRepositoryError) else type(exc).__name__
        harness.errors.append({'path': request.path, 'code': code})
        harness.write_state()
        return jsonify(error=code), 400 if isinstance(exc, (ValueError, stripe.SignatureVerificationError)) else 503

    server = make_server('127.0.0.1', port, app, threaded=True, request_handler=QuietHTTP)
    def stop(*_): threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    print('Sandbox receiver ready; no provider calls made. See the private state file.', flush=True)
    try: server.serve_forever()
    finally:
        server.server_close(); StudioRunHTTPAcceptance.doClassCleanups()


if __name__ == '__main__': main()
