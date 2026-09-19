"""Owned outcome resources. Root registers this factory under /api/v2."""
import os
import hashlib

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from backend.extensions import limiter
from backend.routes.v2_routes import (authenticated, account_limit_key, body, failure,
    repository_error, invalid_json, too_large, rate_limited)
from backend.services.migration_repository import InvalidInputError, MigrationRepositoryError, MigrationNotFoundError, _strict_uuid
from backend.services.migration_planning_service import envelope, validate_key
from backend.services.migration_verification_service import MigrationVerificationService, VerificationEntitlementError
from backend.services.migration_monitoring_service import MigrationMonitoringService


def _disabled():
    flag = 'MCP_PIVOT_MONITORING_ENABLED' if '/monitoring' in request.path else 'MCP_PIVOT_VERIFICATION_ENABLED'
    return os.getenv('MCP_PIVOT_ENABLED', 'false').lower() != 'true' or os.getenv(flag, 'false').lower() != 'true'


def _artifact_service():
    from backend.services.migration_artifact_service import MigrationArtifactService
    return MigrationArtifactService()


def create_migration_outcome_blueprint(verification_factory=None, monitoring_factory=None, artifact_factory=None):
    verify = verification_factory or MigrationVerificationService
    monitor = monitoring_factory or MigrationMonitoringService
    artifacts = artifact_factory or _artifact_service
    bp = Blueprint('migration_outcomes', __name__)

    @bp.record_once
    def init_limiter(state):
        if 'limiter' not in state.app.extensions:
            limiter.init_app(state.app)

    @bp.before_request
    def gate():
        # Runs before authorization or factories, including when registered by a
        # host that forgot its own feature gate. Disabled means no DB calls.
        flag = 'MCP_PIVOT_MONITORING_ENABLED' if '/monitoring' in request.path else 'MCP_PIVOT_VERIFICATION_ENABLED'
        if os.getenv('MCP_PIVOT_ENABLED', 'false').lower() != 'true' or os.getenv(flag, 'false').lower() != 'true':
            return failure('unavailable', 'Migration outcomes are unavailable.', 503, True, 'retry')

    for error, handler in ((MigrationRepositoryError, repository_error), (BadRequest, invalid_json),
                           (UnsupportedMediaType, invalid_json), (RequestEntityTooLarge, too_large), (429, rate_limited)):
        bp.register_error_handler(error, handler)

    @bp.errorhandler(VerificationEntitlementError)
    def entitlement(exc):
        return failure(exc.code, str(exc), 402 if exc.code == 'payment_required' else 409,
                       False, 'complete_payment' if exc.code == 'payment_required' else 'install_artifact' if exc.code == 'not_ready' else 'none')

    def page(allow_monitor=False):
        allowed = {'after', 'limit', 'monitoring_id'} if allow_monitor else {'after', 'limit'}
        if set(request.args) - allowed or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise InvalidInputError('Use after and limit once each.')
        try:
            return {'after': int(request.args.get('after', '-1')), 'limit': int(request.args.get('limit', '100'))}
        except ValueError:
            raise InvalidInputError('Use integer pagination values.') from None

    def installation_input(value):
        if value.get('deployment_confirmation') is not True or not isinstance(value.get('origin_rewrites'), dict):
            raise InvalidInputError('Confirm installation and supply explicit origin_rewrites ({} preserves artifact destinations).')
        if not isinstance(value.get('live_origin'), str) or not value['live_origin'].strip():
            raise InvalidInputError('Supply the explicit live_origin.')
        if value.get('installation_report') is not None and not isinstance(value['installation_report'], dict):
            raise InvalidInputError('installation_report must be an object.')

    def no_query():
        if request.args:
            raise InvalidInputError('This resource accepts no query parameters.')

    @bp.post('/migrations/<migration_id>/artifacts/<artifact_id>/deployments')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def installation(migration_id, artifact_id):
        no_query()
        value = body({'deployment_confirmation', 'live_origin', 'origin_rewrites', 'installation_report', 'idempotency_key'})
        installation_input(value)
        value['idempotency_key'] = validate_key(value.get('idempotency_key'))
        result = artifacts().report_installation(request.api_user_id, migration_id, artifact_id,
            value.get('live_origin'), idempotency_key=value.get('idempotency_key'),
            origin_rewrites=value['origin_rewrites'], installation_report=value.get('installation_report'))
        # Large immutable inputs stay in storage, not the control-plane response.
        data = {key: result[key] for key in ('deployment_id', 'artifact_id', 'live_origin', 'status', 'replayed') if key in result}
        return jsonify(envelope(migration_id, status='succeeded', next_action='verify_redirects', data=data))

    @bp.post('/migrations/<migration_id>/verifications')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def start_verification(migration_id):
        no_query()
        value = body({'artifact_id', 'deployment_id', 'idempotency_key', 'deployment_confirmation', 'live_origin', 'origin_rewrites'})
        key = validate_key(value.get('idempotency_key'))
        deployment = value.get('deployment_id')
        installation_fields = {'deployment_confirmation', 'live_origin', 'origin_rewrites'}
        if deployment:
            if installation_fields.intersection(value):
                raise InvalidInputError('Use an existing deployment_id or explicit installation confirmation, not both.')
        else:
            installation_input(value)
            result = artifacts().report_installation(request.api_user_id, migration_id, value.get('artifact_id'),
                value.get('live_origin'), idempotency_key='verify-install-' + hashlib.sha256(key.encode()).hexdigest(),
                origin_rewrites=value['origin_rewrites'])
            deployment = result['deployment_id']
        return jsonify(verify().start(request.api_user_id, migration_id, value.get('artifact_id'), deployment, key))

    @bp.get('/migrations/<migration_id>/verifications/<verification_id>')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def verification_status(migration_id, verification_id):
        no_query()
        return jsonify(verify().status(request.api_user_id, migration_id, verification_id))

    @bp.get('/migrations/<migration_id>/verifications/<verification_id>/issues')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def verification_issues(migration_id, verification_id):
        return jsonify(envelope(migration_id, status='succeeded', data=verify().issues(
            request.api_user_id, migration_id, verification_id, **page())))

    @bp.post('/migrations/<migration_id>/monitoring')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def manage_monitoring(migration_id):
        no_query()
        value = body({'action', 'artifact_id', 'deployment_id', 'monitoring_id', 'subscription_id', 'alert_email', 'idempotency_key'})
        return jsonify(monitor().manage(request.api_user_id, migration_id, value.pop('action', None), **value))

    @bp.get('/migrations/<migration_id>/monitoring/<monitoring_id>')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def monitoring_status(migration_id, monitoring_id):
        no_query()
        return jsonify(monitor().status(request.api_user_id, migration_id, monitoring_id))

    @bp.get('/migrations/<migration_id>/monitoring/<monitoring_id>/fixes')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def monitoring_fixes(migration_id, monitoring_id):
        return jsonify(monitor().fixes(request.api_user_id, migration_id, monitoring_id, **page()))


    def selected_monitor(service, migration_id, *, paginated=False):
        allowed = {'monitoring_id', 'after', 'limit'} if paginated else {'monitoring_id'}
        if set(request.args) - allowed or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise InvalidInputError('Use supported query parameters once each.')
        selected = request.args.get('monitoring_id')
        if selected is not None:
            return _strict_uuid(selected, 'monitoring_id')[1]
        _, owner = _strict_uuid(request.api_user_id, 'user_id')
        _, migration = _strict_uuid(migration_id, 'migration_id')
        rows = service.repository._execute(service.repository.client.table('migration_monitors').select('id')
            .eq('user_id', owner).eq('migration_id', migration).order('created_at', desc=True).order('id', desc=True).limit(1)).data
        if not rows:
            raise MigrationNotFoundError('Monitoring site not found.')
        return str(rows[0]['id'])

    @bp.get('/migrations/<migration_id>/monitoring')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def current_monitoring_status(migration_id):
        service = monitor()
        return jsonify(service.status(request.api_user_id, migration_id, selected_monitor(service, migration_id)))

    @bp.get('/migrations/<migration_id>/monitoring/fixes')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def current_monitoring_fixes(migration_id):
        service = monitor()
        selected = selected_monitor(service, migration_id, paginated=True)
        return jsonify(service.fixes(request.api_user_id, migration_id, selected, **page(allow_monitor=True)))

    return bp
