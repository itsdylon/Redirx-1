"""Owned artifact creation and downloads; content is never a public storage URL."""
import os

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from backend.extensions import limiter
from backend.routes.v2_routes import (authenticated, account_limit_key, body, failure,
    repository_error, invalid_json, too_large, rate_limited)
from backend.services.migration_artifact_service import MigrationArtifactService
from backend.services.migration_planning_service import envelope
from backend.services.migration_repository import InvalidInputError, MigrationRepositoryError


def _disabled():
    return os.getenv('MCP_PIVOT_ENABLED', 'false').lower() != 'true'


def create_migration_artifact_blueprint(service_factory=None):
    factory = service_factory or MigrationArtifactService
    bp = Blueprint('migration_artifacts', __name__)

    @bp.record_once
    def init_limiter(state):
        if 'limiter' not in state.app.extensions:
            limiter.init_app(state.app)

    @bp.before_request
    def gate():
        if _disabled():
            return failure('unavailable', 'Migration artifacts are unavailable.', 503, True, 'retry')
        if request.args:
            raise InvalidInputError('This resource accepts no query parameters.')

    for error, handler in ((MigrationRepositoryError, repository_error), (BadRequest, invalid_json),
                           (UnsupportedMediaType, invalid_json), (RequestEntityTooLarge, too_large), (429, rate_limited)):
        bp.register_error_handler(error, handler)

    @bp.post('/migrations/<migration_id>/artifacts')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def create_artifact(migration_id):
        value = body({'run_id', 'format', 'revision', 'allow_partial', 'idempotency_key'})
        if type(value.get('revision')) is not int or not 0 <= value['revision'] <= 2_147_483_647:
            raise InvalidInputError('revision must be a nonnegative selection revision.')
        if type(value.get('allow_partial', False)) is not bool:
            raise InvalidInputError('allow_partial must be a boolean.')
        row = factory().create_artifact(request.api_user_id, migration_id, value.get('run_id'),
            fmt=value.get('format'), selection_revision=str(value['revision']),
            partial_policy='allow' if value.get('allow_partial', False) else 'deny',
            idempotency_key=value.get('idempotency_key'))
        data = {key: row.get(key) for key in ('run_id', 'decision_revision', 'format', 'content_hash',
            'target_origins', 'included_count', 'excluded_count', 'partial_policy', 'replayed')}
        data['artifact_id'] = row['id']
        data['download_url'] = f'/api/v2/migrations/{migration_id}/artifacts/{row["id"]}'
        data['resource_uri'] = f'redirx://migrations/{migration_id}/artifacts/{row["id"]}'
        data['summary'] = 'Immutable artifact ready; install it before confirming deployment.'
        response = jsonify(envelope(migration_id, status='succeeded', next_action='install_artifact', data=data))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/migrations/<migration_id>/artifacts/<artifact_id>')
    @authenticated
    @limiter.limit('60 per minute', key_func=account_limit_key, exempt_when=_disabled)
    def download_artifact(migration_id, artifact_id):
        data = factory().authorize_download(request.api_user_id, migration_id, artifact_id)
        response = jsonify(envelope(migration_id, status='succeeded', next_action='install_artifact', data=data))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return bp
