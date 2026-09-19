"""Owned mapping reads and audited decisions using the authoritative 038 RPCs."""
import base64
import binascii
import json

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from backend.extensions import limiter
from backend.routes.v2_routes import (authenticated, account_limit_key, body, failure,
    repository_error, invalid_json, too_large, rate_limited)
from backend.services.mapping_decision_service import MappingDecisionService
from backend.services.migration_planning_service import envelope, validate_key
from backend.services.migration_repository import MigrationRepositoryError, InvalidInputError, _strict_uuid


def _scope(owner, migration, run, match_filter):
    return [_strict_uuid(owner, 'user_id')[1], _strict_uuid(migration, 'migration_id')[1],
            _strict_uuid(run, 'run_id')[1], match_filter]


def _cursor(token, scope):
    if token is None:
        return None
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise ValueError()
        decoded = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        value = json.loads(decoded)
        if not isinstance(value, dict) or set(value) != {'v', 'scope', 'position'} or type(value['v']) is not int or value['v'] != 1 or value['scope'] != scope:
            raise ValueError()
        position = value['position']
        if not isinstance(position, dict) or set(position) != {'observed', 'clicks', 'id'}:
            raise ValueError()
        if type(position['observed']) is not bool or type(position['clicks']) is not int or not -1 <= position['clicks'] <= 9223372036854775807:
            raise ValueError()
        _strict_uuid(position['id'], 'cursor')
        return position
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise InvalidInputError('Invalid cursor for this run and filter.') from None


def _encode(position, scope):
    if position is None:
        return None
    # This is opaque transport encoding, not authorization. Every list request
    # still checks owned migration/run scope in SQL before returning any rows.
    raw = json.dumps({'v': 1, 'scope': scope, 'position': position}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def create_migration_mapping_blueprint(service_factory=None):
    factory = service_factory or MappingDecisionService
    blueprint = Blueprint('migration_mapping', __name__)

    @blueprint.record_once
    def init_limiter(state):
        if 'limiter' not in state.app.extensions:
            limiter.init_app(state.app)

    for error, handler in ((MigrationRepositoryError, repository_error), (BadRequest, invalid_json),
                           (UnsupportedMediaType, invalid_json), (RequestEntityTooLarge, too_large), (429, rate_limited)):
        blueprint.register_error_handler(error, handler)

    @blueprint.get('/migrations/<migration_id>/runs/<run_id>/matches')
    @authenticated
    @limiter.limit('120 per minute', key_func=account_limit_key)
    def list_matches(migration_id, run_id):
        if set(request.args) - {'filter', 'cursor', 'limit'} or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise InvalidInputError('Provide supported, single-valued mapping query parameters.')
        raw_limit = request.args.get('limit', '100')
        if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 3:
            raise InvalidInputError('limit must be an integer between 1 and 500.')
        match_filter = request.args.get('filter', 'all')
        scope = _scope(request.api_user_id, migration_id, run_id, match_filter)
        result = factory().list_matches(request.api_user_id, migration_id, run_id,
            match_filter=match_filter, cursor=_cursor(request.args.get('cursor'), scope), limit=int(raw_limit))
        result['next_cursor'] = _encode(result['next_cursor'], scope)
        return jsonify(envelope(migration_id, status='succeeded', next_action='resolve_matches', data={
            'summary': 'Owned mappings in traffic order; inspect per-row evidence before deciding.',
            'run_id': run_id, **result}))

    @blueprint.patch('/migrations/<migration_id>/runs/<run_id>/matches')
    @authenticated
    @limiter.limit('30 per minute', key_func=account_limit_key)
    def resolve_matches(migration_id, run_id):
        value = body({'decisions', 'idempotency_key'})
        validate_key(value.get('idempotency_key'))
        decisions = value.get('decisions')
        if isinstance(decisions, list):
            for item in decisions:
                if isinstance(item, dict) and not isinstance(item.get('action'), str):
                    raise InvalidInputError('action must be a supported string.')
                if isinstance(item, dict) and any(isinstance(v, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in v) for v in item.values()):
                    raise InvalidInputError('Mapping decision text must contain valid Unicode.')
                if isinstance(item, dict) and set(item) - {'mapping_id', 'expected_revision', 'action', 'target_url', 'rationale'}:
                    raise InvalidInputError('Unsupported mapping decision fields.')
                if isinstance(item, dict) and type(item.get('expected_revision')) is int and item['expected_revision'] > 2147483647:
                    raise InvalidInputError('expected_revision is outside the supported range.')
        result = factory().resolve_matches(request.api_user_id, migration_id, run_id, decisions, value.get('idempotency_key'))
        applied = sum(item.get('code') == 'ok' for item in result['outcomes'])
        failed = len(result['outcomes']) - applied
        return jsonify(envelope(migration_id, result['operation_id'], status='partial' if failed else 'succeeded',
            next_action='resolve_matches', data={**result, 'applied': applied, 'not_applied': failed,
                'summary': f'{applied} mapping decisions applied; {failed} require further input.'}))

    return blueprint
