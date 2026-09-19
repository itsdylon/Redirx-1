"""Opt-in agent GSC routes; root app registers under /api/v2."""
from flask import Blueprint, jsonify, request, make_response
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from backend.extensions import limiter
from backend.routes.v2_routes import authenticated, account_limit_key, body, failure, repository_error
from backend.services.migration_repository import MigrationRepositoryError
from backend.services.migration_gsc_service import MigrationGSCService


def create_migration_gsc_blueprint(service_factory=None):
    factory = service_factory or MigrationGSCService
    blueprint = Blueprint('migration_gsc', __name__)

    @blueprint.record_once
    def init_limiter(state):
        if 'limiter' not in state.app.extensions:
            limiter.init_app(state.app)

    blueprint.register_error_handler(MigrationRepositoryError, repository_error)
    blueprint.register_error_handler(BadRequest, lambda exc: failure('invalid_input', 'Provide a JSON object.', 400))
    blueprint.register_error_handler(UnsupportedMediaType, lambda exc: failure('invalid_input', 'Provide a JSON object.', 400))

    @blueprint.post('/connections/search-console/actions')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key)
    def actions():
        value = body({'action', 'migration_id', 'property', 'start_date', 'end_date', 'idempotency_key'})
        return jsonify(factory().execute(request.api_user_id, value.pop('action', None), **value))

    @blueprint.get('/connections/search-console/callback')
    @limiter.limit('60 per minute')
    def callback():
        # No identity from query parameters and no caller-provided redirect.
        try:
            if any(len(request.args.getlist(key)) != 1 for key in request.args):
                raise ValueError()
            result = factory().callback(request.args.get('state'), request.args.get('code'), request.args.get('error'))
            success = result['status'] == 'succeeded'
            message = ('Search Console connected. Return to your agent and select a property to sync traffic.' if success else
                       'Search Console was not connected. Return to your agent to reconnect, or continue without traffic data.')
            response = make_response(message, 200 if success else 400)
        except (MigrationRepositoryError, ValueError):
            response = make_response('This Search Console link is invalid or expired. Return to your agent and reconnect.', 400)
        response.headers.update({'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"})
        return response

    return blueprint
