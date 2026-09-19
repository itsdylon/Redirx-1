"""Opt-in discovery resources; register under /api/v2 with the shared v2 auth."""
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from backend.extensions import limiter
from backend.routes.v2_routes import (authenticated, account_limit_key, body, failure,
    repository_error, invalid_json, too_large, rate_limited)
from backend.services.migration_discovery_service import MigrationDiscoveryService
from backend.services.migration_repository import MigrationRepositoryError, InvalidInputError


def create_migration_discovery_blueprint(service_factory=None):
    factory = service_factory or MigrationDiscoveryService
    blueprint = Blueprint('migration_discovery', __name__)

    @blueprint.record_once
    def init_limiter(state):
        if 'limiter' not in state.app.extensions:
            limiter.init_app(state.app)

    for error, handler in ((MigrationRepositoryError, repository_error), (BadRequest, invalid_json),
                           (UnsupportedMediaType, invalid_json), (RequestEntityTooLarge, too_large), (429, rate_limited)):
        blueprint.register_error_handler(error, handler)

    @blueprint.post('/migrations/<migration_id>/discoveries')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key)
    def start(migration_id):
        value = body({'side', 'idempotency_key', 'crawl', 'include_gsc', 'cms', 'limits'})
        return jsonify(factory().start(request.api_user_id, migration_id,
            value.pop('side', None), value.pop('idempotency_key', None), **value))

    @blueprint.get('/migrations/<migration_id>/discoveries/<operation_id>')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key)
    def get(migration_id, operation_id):
        if request.args:
            raise InvalidInputError('Discovery status accepts no query parameters.')
        return jsonify(factory().get(request.api_user_id, migration_id, operation_id))

    @blueprint.post('/migrations/<migration_id>/discoveries/<operation_id>/cancel')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key)
    def cancel(migration_id, operation_id):
        body(set())
        result = factory().cancel(request.api_user_id, migration_id, operation_id)
        if result['status'] in ('queued', 'running'):
            # 044 does not persist cancellation intent while another lease owns
            # the step. Make the required retry explicit, never report cancelled.
            result['next_action'] = 'retry'
            result['data']['summary'] = 'A discovery step holds the lease. Retry cancellation after it releases.'
            result['data']['cancellation_pending'] = False
            result['retry_after_seconds'] = 5
        return jsonify(result)

    return blueprint
