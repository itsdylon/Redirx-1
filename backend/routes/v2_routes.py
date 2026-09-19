"""Opt-in durable planning and explicit inventory resources."""
from functools import wraps

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from backend.extensions import limiter
from backend.services.api_key_service import ApiKeyService, looks_like_api_key
from backend.services.mcp_delegation_service import MCPDelegationService
from backend.services.inventory_import_service import InventoryImportService
from backend.services.migration_repository import InvalidInputError, MigrationRepositoryError
from backend.services.migration_planning_service import MigrationPlanningService, envelope

v2_blueprint = Blueprint('v2', __name__)


@v2_blueprint.record_once
def init_limiter(state):
    if 'limiter' not in state.app.extensions:
        limiter.init_app(state.app)


def failure(code, message, http_status, retryable=False, next_action='none'):
    return jsonify(envelope(status='failed', next_action=next_action, error={
        'code': code, 'message': message, 'retryable': retryable, 'next_action': next_action,
    })), http_status


def resolve_authorization():
    # Flask-Limiter can evaluate a route limit before its view decorator runs.
    # Resolve once per request so rotating delegations still share account limits.
    if getattr(request, '_v2_auth_resolved', False):
        return request.api_user_id
    request._v2_auth_resolved = True
    header = request.headers.get('Authorization', '')
    token = header[7:].strip() if header.startswith('Bearer ') else ''
    user = None
    if token:
        user = ApiKeyService().resolve(token) if looks_like_api_key(token) else MCPDelegationService().resolve(token)
    request.api_user_id = user
    return user


def account_limit_key():
    return resolve_authorization() or request.remote_addr or 'unknown'


def authenticated(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not resolve_authorization():
            return failure('reconnect_required', 'Provide a valid API key or MCP delegation.', 401, next_action='reconnect')
        return fn(*args, **kwargs)
    return wrapped


@v2_blueprint.errorhandler(MigrationRepositoryError)
def repository_error(exc):
    code = 'operation_conflict' if exc.code == 'inventory_busy' else exc.code
    http_status = {'not_found': 404, 'invalid_input': 400, 'operation_conflict': 409,
                   'capacity_exceeded': 413}.get(code, 503)
    return failure(code, str(exc), http_status, exc.retryable, 'retry' if exc.retryable else 'none')


@v2_blueprint.errorhandler(BadRequest)
@v2_blueprint.errorhandler(UnsupportedMediaType)
def invalid_json(exc):
    return failure('invalid_input', 'Provide a JSON object.', 400)


@v2_blueprint.errorhandler(RequestEntityTooLarge)
def too_large(exc):
    return failure('capacity_exceeded', 'The request exceeds the available capacity.', 413)


@v2_blueprint.errorhandler(429)
def rate_limited(exc):
    payload = envelope(status='failed', next_action='retry', error={
        'code': 'rate_limited', 'message': 'Retry after the rate limit window.',
        'retryable': True, 'next_action': 'retry',
    })
    payload['retry_after_seconds'] = 60
    return jsonify(payload), 429, {'Retry-After': '60'}


def body(allowed=None):
    value = request.get_json()
    if not isinstance(value, dict) or (allowed is not None and set(value) - allowed):
        raise InvalidInputError('Provide a JSON object with supported fields.')
    return value


@v2_blueprint.post('/migrations')
@authenticated
@limiter.limit('30 per minute', key_func=account_limit_key)
def plan_migration():
    return jsonify(MigrationPlanningService().plan(request.api_user_id, body()))


@v2_blueprint.get('/migrations/<migration_id>')
@authenticated
@limiter.limit('120 per minute', key_func=account_limit_key)
def get_migration(migration_id):
    service = MigrationPlanningService()
    if request.args.get('operation_id'):
        return jsonify(service.operation(request.api_user_id, request.args['operation_id'], migration_id))
    return jsonify(service.get(request.api_user_id, migration_id))


@v2_blueprint.post('/migrations/<migration_id>/inventories')
@authenticated
@limiter.limit('30 per minute', key_func=account_limit_key)
def import_inventory(migration_id):
    value = body({'side', 'rows', 'idempotency_key', 'host_aliases'})
    service = MigrationPlanningService()
    migration = service.repository.get_migration(request.api_user_id, migration_id)
    side = value.get('side')
    if side not in ('old', 'new'):
        raise InvalidInputError("side must be 'old' or 'new'.")
    # Aliases must be explicitly declared for THIS owned migration at planning.
    # A request cannot silently expand the side's target scope during import.
    aliases = migration.get('site_aliases', {}).get(side, [])
    if 'host_aliases' in value and value['host_aliases'] != aliases:
        raise InvalidInputError('Import aliases must match the migration declaration.')
    result = InventoryImportService(service.repository).import_inventory(
        request.api_user_id, migration_id, side, value.get('rows'), value.get('idempotency_key'), aliases)
    response = service.operation(request.api_user_id, result['operation_id'], migration_id)
    response['data']['replayed'] = result['replayed']
    return jsonify(response)


@v2_blueprint.get('/operations/<operation_id>')
@authenticated
@limiter.limit('120 per minute', key_func=account_limit_key)
def operation_status(operation_id):
    return jsonify(MigrationPlanningService().operation(request.api_user_id, operation_id))
