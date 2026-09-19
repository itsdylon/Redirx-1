"""Optional Google consent and traffic data for durable migrations.

Only a browser receives the Google consent URL. Tokens stay in the existing
service-only connection store; callbacks consume durable state exactly once.
"""
from __future__ import annotations

import base64
from datetime import date, timedelta
import hashlib
import hmac
import os
from urllib.parse import urlencode, urlsplit

import requests

from src.redirx.config import Config
from .gsc_service import (GSCService, GSCError, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL,
                          GOOGLE_REVOKE_URL, GSC_QUERY_URL, GSC_SITES_URL, GSC_SCOPES, HTTP_TIMEOUT)
from .inventory_policy import canonical_url_identity, InventoryPolicyError
from .migration_repository import MigrationRepository, InvalidInputError, OperationConflictError, _strict_uuid
from .migration_planning_service import envelope, validate_key

READ_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
MAX_ROWS = 50000


def covers(property, url):
    """Google URL-prefix means exact scheme/authority/path prefix, not aliases."""
    try:
        target = urlsplit(canonical_url_identity(url))
        if property.startswith('sc-domain:'):
            domain = property[10:].lower()
            return bool(domain) and (target.hostname == domain or target.hostname.endswith('.' + domain))
        prefix = urlsplit(canonical_url_identity(property))
        return (prefix.scheme, prefix.netloc) == (target.scheme, target.netloc) and target.path.startswith(prefix.path)
    except (ValueError, TypeError, AttributeError, InventoryPolicyError):
        return False


def data_window(start=None, end=None):
    latest = date.today() - timedelta(days=2)
    if (start is None) != (end is None):
        raise InvalidInputError('Provide both start_date and end_date, or omit both.')
    if start is None:
        return (latest - timedelta(days=27)).isoformat(), latest.isoformat()
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end or not 0 <= (last-first).days < 90 or last > latest or first < date.today()-timedelta(days=480):
            raise ValueError()
    except (ValueError, TypeError):
        raise InvalidInputError('Use a 1–90 day ISO date window within the last 480 days, ending at least two days ago.') from None
    return start, end


class AgentGSCProvider(GSCService):
    """Reuse token storage/expiry and fixed Google endpoints, with safe failures."""
    def exchange(self, code, redirect_uri, verifier):
        response = requests.post(GOOGLE_TOKEN_URL, data={
            'code': code, 'client_id': Config.GOOGLE_OAUTH_CLIENT_ID,
            'client_secret': Config.GOOGLE_OAUTH_CLIENT_SECRET, 'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code', 'code_verifier': verifier,
        }, timeout=HTTP_TIMEOUT)
        if not response.ok:
            raise GSCError('reconnect_required', 'Google could not complete consent. Reconnect and approve Search Console access.', 409)
        tokens = response.json()
        if not tokens.get('access_token') or not tokens.get('refresh_token') or READ_SCOPE not in tokens.get('scope', '').split():
            raise GSCError('reconnect_required', 'Reconnect and grant offline Search Console read access.', 409)
        return {'access_token': tokens['access_token'], 'refresh_token': tokens['refresh_token'],
                'token_expires_at': self._expiry_iso(tokens.get('expires_in')), 'scopes': tokens['scope']}

    def _refresh_access_token(self, user_id, refresh_token):
        response = requests.post(GOOGLE_TOKEN_URL, data={
            'refresh_token': refresh_token, 'client_id': Config.GOOGLE_OAUTH_CLIENT_ID,
            'client_secret': Config.GOOGLE_OAUTH_CLIENT_SECRET, 'grant_type': 'refresh_token',
        }, timeout=HTTP_TIMEOUT)
        if not response.ok:
            # Transient outages must not destroy a recoverable refresh token.
            if response.status_code == 400 and response.json().get('error') == 'invalid_grant':
                raise GSCError('reconnect_required', 'Your Search Console access expired or was revoked. Reconnect.', 409)
            raise GSCError('origin_unavailable', 'Google is temporarily unavailable. Retry later.', 503)
        tokens = response.json()
        if not tokens.get('access_token'):
            raise GSCError('origin_unavailable', 'Google did not return usable access. Retry later.', 503)
        self.connection_db.update_access_token(user_id, tokens['access_token'], self._expiry_iso(tokens.get('expires_in')))
        return tokens['access_token']

    def list_properties(self, user_id):
        token = self._get_valid_access_token(user_id)
        response = requests.get(GSC_SITES_URL, headers={'Authorization': 'Bearer ' + token}, timeout=HTTP_TIMEOUT)
        if not response.ok:
            if response.status_code in (401, 403):
                raise GSCError('reconnect_required', 'Google denied access. Reconnect and check property permissions or app access restrictions.', 409)
            raise GSCError('origin_unavailable', 'Google properties are temporarily unavailable. Retry later.', 503)
        entries = response.json().get('siteEntry', [])
        return [{'site_url': e['siteUrl'], 'permission_level': e['permissionLevel']} for e in entries
                if isinstance(e, dict) and isinstance(e.get('siteUrl'), str)
                and e.get('permissionLevel') in ('siteOwner', 'siteFullUser', 'siteRestrictedUser')]

    def analytics(self, user_id, property, start, end):
        token = self._get_valid_access_token(user_id)
        rows = []
        while len(rows) < MAX_ROWS:
            response = requests.post(GSC_QUERY_URL.format(site=requests.utils.quote(property, safe='')),
                headers={'Authorization': 'Bearer ' + token}, json={
                    'startDate': start, 'endDate': end, 'dimensions': ['page'], 'dataState': 'final',
                    'rowLimit': 25000, 'startRow': len(rows)}, timeout=HTTP_TIMEOUT)
            if not response.ok:
                if response.status_code in (401, 403):
                    raise GSCError('reconnect_required', 'Google denied Search Console access. Reconnect and check property permissions.', 409)
                raise GSCError('origin_unavailable', 'Search Console data is temporarily unavailable. Retry later.', 503)
            batch = response.json().get('rows', [])
            if not isinstance(batch, list) or len(batch) > 25000:
                raise GSCError('origin_unavailable', 'Google returned an invalid data page. Retry later.', 503)
            rows.extend(batch)
            if len(batch) < 25000:
                return rows, False
        return rows, True


class GSCRepository:
    def __init__(self, repository=None):
        self.base = repository if repository is not None else MigrationRepository()
        self.client = self.base.client

    def rpc(self, name, args):
        return self.base._execute(self.client.rpc(name, args)).data

    def account(self, user):
        rows = self.base._execute(self.client.table('gsc_agent_accounts').select('state').eq('user_id', user)).data
        return rows[0] if rows else {'state': 'disconnected'}

    def selection(self, user, migration):
        rows = self.base._execute(self.client.table('gsc_migration_selections').select('property,start_date,end_date,synced_at,coverage').eq('user_id', user).eq('migration_id', migration)).data
        return rows[0] if rows else None

    def connection(self, user):
        rows = self.base._execute(self.client.table('gsc_connections').select('*').eq('user_id', user)).data
        return rows[0] if rows else None

    def discovery_page(self, user, migration, after, limit):
        self.base.get_migration(user, migration)
        query = self.client.table('gsc_migration_metrics').select('url,clicks,impressions').eq('migration_id', migration).order('url').limit(limit + 1)
        if after is not None:
            query = query.gt('url', after)
        return self.base._execute(query).data

    def metric_rows(self, user, migration, urls):
        self.base.get_migration(user, migration)
        if not urls:
            return []
        return self.base._execute(self.client.table('gsc_migration_metrics').select('url,clicks,impressions').eq('migration_id', migration).in_('url', urls)).data


class MigrationGSCService:
    def __init__(self, repository=None, provider=None, *, store=None, redirect_uri=None, state_secret=None):
        self.store = store if store is not None else GSCRepository(repository)
        self.repository = self.store.base
        self._provider = provider
        self.redirect_uri = redirect_uri or os.environ.get('GSC_AGENT_REDIRECT_URI', '')
        self.secret = state_secret or Config.GSC_STATE_SECRET

    @property
    def provider(self):
        if self._provider is None:
            try:
                self._provider = AgentGSCProvider()
            except ValueError:
                raise GSCError('not_ready', 'Search Console is not configured. You can continue without it.', 503) from None
        return self._provider

    def _secret_value(self, purpose, value):
        if not self.secret or len(self.secret) < 32:
            raise GSCError('not_ready', 'Search Console is not configured. You can continue without it.', 503)
        return base64.urlsafe_b64encode(hmac.digest(self.secret.encode(), (purpose + ':' + value).encode(), 'sha256')).decode().rstrip('=')

    def _consent(self, user, key):
        try:
            uri = urlsplit(self.redirect_uri)
            if uri.scheme != 'https' or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
                raise ValueError()
        except ValueError:
            raise GSCError('not_ready', 'Search Console callback is not configured. You can continue without it.', 503) from None
        # Instantiate first so a disabled deployment never mints an unusable link.
        self.provider
        state = self._secret_value('state', user + ':' + key)
        verifier = self._secret_value('pkce', state)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return state, GOOGLE_AUTH_URL + '?' + urlencode({
            'client_id': Config.GOOGLE_OAUTH_CLIENT_ID, 'redirect_uri': self.redirect_uri,
            'response_type': 'code', 'scope': GSC_SCOPES, 'access_type': 'offline',
            'prompt': 'consent', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
        })

    def _finish(self, op, result, *, state=None, tokens=None, metrics=None):
        return self.store.rpc('finish_gsc_agent_operation', {'p_user_id': op['user_id'], 'p_id': op['id'],
            'p_result': result, 'p_state': state, 'p_tokens': tokens, 'p_metrics': metrics})

    @staticmethod
    def _failure(error, migration=None, operation=None):
        reconnect = error.code in ('reconnect_required', 'gsc_reauth_required', 'gsc_not_connected')
        code = 'reconnect_required' if reconnect else error.code if error.code in ('not_ready', 'origin_unavailable', 'invalid_input') else 'origin_unavailable'
        action = 'connect_search_console' if reconnect else 'none' if code == 'not_ready' else 'retry'
        return envelope(migration, operation, status='needs_input' if reconnect else 'failed', next_action=action,
            data={'optional': True, 'connection_state': 'reconnect_required' if reconnect else 'unavailable'},
            error={'code': code, 'message': error.user_message, 'retryable': code == 'origin_unavailable', 'next_action': action})

    def execute(self, user_id, action, *, migration_id=None, property=None, start_date=None, end_date=None, idempotency_key=None):
        _, user = _strict_uuid(user_id, 'user_id')
        if action not in ('connect', 'status', 'properties', 'disconnect', 'sync'):
            raise InvalidInputError('Unsupported Search Console action.')
        migration = None
        if migration_id is not None:
            migration = self.repository.get_migration(user, migration_id)
            migration_id = migration['id']
        if action != 'sync' and any(v is not None for v in (property, start_date, end_date)):
            raise InvalidInputError('Property and data window are only accepted for sync.')
        if action in ('connect', 'disconnect', 'sync'):
            validate_key(idempotency_key)
        if action == 'sync':
            if migration is None or not isinstance(property, str) or not property or len(property) > 2048:
                raise InvalidInputError('Sync requires an owned migration and an explicit Search Console property.')
            start_date, end_date = data_window(start_date, end_date)
        payload = {'migration_id': migration_id}
        if action == 'sync':
            payload.update(property=property, start_date=start_date, end_date=end_date)
        op = None
        try:
            if action in ('status', 'properties'):
                account = self.store.account(user)
                connected = self.provider.get_status(user).get('connected', False)
                if account['state'] == 'reconnect_required':
                    return self._failure(GSCError('reconnect_required', 'Reconnect Google Search Console.'), migration_id)
                properties = self.provider.list_properties(user) if connected else []
                data = {'optional': True, 'connection_state': 'connected' if connected else account['state'], 'properties': properties}
                if migration_id:
                    data['selection'] = self.store.selection(user, migration_id)
                return envelope(migration_id, status='succeeded', next_action='none' if connected else 'connect_search_console', data=data)
            state, url = self._consent(user, idempotency_key) if action == 'connect' else (None, None)
            op = self.store.rpc('reserve_gsc_agent_operation', {'p_user_id': user, 'p_action': action,
                'p_key': idempotency_key, 'p_request': payload,
                'p_state_hash': hashlib.sha256(state.encode()).hexdigest() if state else None})
            if op.get('result') is not None:
                return op['result']
            if action == 'connect':
                if op['status'] != 'awaiting_consent':
                    raise GSCError('reconnect_required', 'This consent link was already used. Connect again with a new idempotency key.')
                from datetime import datetime, timezone
                if datetime.fromisoformat(str(op['expires_at']).replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                    raise GSCError('reconnect_required', 'This consent link expired. Connect again with a new idempotency key.')
                return envelope(migration_id, op['id'], status='needs_input', next_action='connect_search_console', data={
                    'optional': True, 'authorization_url': url, 'expires_at': op['expires_at'], 'connection_state': 'connecting'})
            if op.get('replayed'):
                # A process may have stopped mid-network-call. A new key is safe:
                # no charge/work grant is consumed by optional traffic sync.
                raise OperationConflictError('Search Console action is still running. Check status or retry with a new key.')
            if action == 'disconnect':
                connection = self.store.connection(user)
                result = self._finish(op, envelope(migration_id, op['id'], status='succeeded', next_action='none',
                    data={'optional': True, 'connection_state': 'disconnected', 'revocation': 'best_effort'}))
                if connection:
                    try:
                        requests.post(GOOGLE_REVOKE_URL, data={'token': connection['refresh_token']}, timeout=HTTP_TIMEOUT)
                    except requests.RequestException:
                        pass
                return result
            allowed = [p['site_url'] for p in self.provider.list_properties(user)]
            origins = [migration['old_origin'], *migration.get('site_aliases', {}).get('old', [])]
            if property not in allowed or not any(covers(property, origin + '/') for origin in origins):
                raise GSCError('invalid_input', 'Choose an accessible property covering an explicitly declared old-site origin.')
            rows, capped = self.provider.analytics(user, property, start_date, end_date)
            metrics = {}
            for row in rows:
                try:
                    url = canonical_url_identity(row['keys'][0])
                    if not covers(property, url) or not any(covers(origin + '/', url) for origin in origins):
                        continue
                    clicks, impressions = row['clicks'], row['impressions']
                    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 <= n <= 9223372036854775807 or int(n) != n for n in (clicks, impressions)):
                        raise ValueError()
                    if url in metrics:
                        # Canonical host spelling duplicates are not separate observations.
                        raise ValueError()
                    metrics[url] = {'url': url, 'clicks': int(clicks), 'impressions': int(impressions)}
                except (KeyError, IndexError, TypeError, ValueError, OverflowError, InventoryPolicyError):
                    raise GSCError('origin_unavailable', 'Search Console returned incomplete or ambiguous metric rows. Retry later.', 503) from None
            return self._finish(op, envelope(migration_id, op['id'], status='partial' if capped else 'succeeded', next_action='none', data={
                'optional': True, 'property': property, 'start_date': start_date, 'end_date': end_date,
                'observed_urls': len(metrics), 'coverage': 'partial' if capped else 'source_limited',
                'summary': 'Observed Search Console metrics saved. Unreturned URLs have unavailable traffic, not measured zero.'}), metrics=list(metrics.values()))
        except (requests.RequestException, ValueError) as exc:
            # Never surface Google response text, authorization codes or tokens.
            return self._handle_error(GSCError('origin_unavailable', 'Google is temporarily unavailable. Retry later.', 503), user, migration_id, op)
        except GSCError as exc:
            return self._handle_error(exc, user, migration_id, op)

    def _handle_error(self, exc, user, migration, op):
        result = self._failure(exc, migration, op['id'] if op else None)
        reconnect = result['error']['code'] == 'reconnect_required'
        if op and op['status'] == 'running' and not op.get('replayed'):
            result['status'] = 'failed'
            return self._finish(op, result, state='reconnect_required' if reconnect else None)
        return result

    def callback(self, state, code=None, error=None):
        if not isinstance(state, str) or len(state) != 43:
            raise InvalidInputError('Invalid or expired Search Console state. Reconnect.')
        op = self.store.rpc('consume_gsc_agent_state', {'p_hash': hashlib.sha256(state.encode()).hexdigest()})
        migration = op['request'].get('migration_id')
        try:
            if error or not isinstance(code, str) or not code or len(code) > 4096:
                result = envelope(migration, op['id'], status='cancelled', next_action='connect_search_console', data={'optional': True, 'connection_state': 'disconnected'})
                return self._finish(op, result, state='disconnected')
            tokens = self.provider.exchange(code, self.redirect_uri, self._secret_value('pkce', state))
            return self._finish(op, envelope(migration, op['id'], status='succeeded', next_action='none',
                data={'optional': True, 'connection_state': 'connected', 'summary': 'Search Console connected. Select a property to sync traffic.'}), state='connected', tokens=tokens)
        except (requests.RequestException, ValueError):
            return self._handle_error(GSCError('reconnect_required', 'Google could not complete consent. Reconnect.'), op['user_id'], migration, op)
        except GSCError as exc:
            return self._handle_error(exc, op['user_id'], migration, op)

    def metrics_for_mappings(self, user_id, migration_id, mappings):
        """Shared transport join: retains IDs and never manufactures zero rows."""
        if not isinstance(mappings, list) or len(mappings) > 500:
            raise InvalidInputError('Join at most 500 mappings per page.')
        self.repository.get_migration(user_id, migration_id)
        identities = [canonical_url_identity(row['old_url']) for row in mappings]
        metrics = {row['url']: row for row in self.store.metric_rows(user_id, migration_id, list(set(identities)))}
        selection = self.store.selection(user_id, migration_id)
        return [{**row, 'traffic': ({'state': 'observed', 'clicks': metrics[key]['clicks'],
                    'impressions': metrics[key]['impressions'], 'source': 'gsc', 'selection': selection}
                if key in metrics else {'state': 'unavailable', 'clicks': None, 'impressions': None, 'source': 'gsc', 'selection': selection})}
                for row, key in zip(mappings, identities)]

    def discovery_rows(self, user_id, migration_id, *, after_url=None, limit=500):
        """Page the GSC-only source into preflight without claiming full coverage."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise InvalidInputError('GSC discovery pages contain 1–500 URLs.')
        if after_url is not None:
            try:
                after_url = canonical_url_identity(after_url)
            except (InventoryPolicyError, TypeError):
                raise InvalidInputError('Invalid GSC URL cursor.') from None
        rows = self.store.discovery_page(user_id, migration_id, after_url, limit)
        selection = self.store.selection(user_id, migration_id)
        return {'items': [{'url': row['url'], 'provenance': ['gsc'], 'metadata': {
                    'traffic_state': 'observed', 'clicks': row['clicks'], 'impressions': row['impressions']}}
                for row in rows[:limit]],
                'next_cursor': rows[limit - 1]['url'] if len(rows) > limit else None,
                'coverage': selection['coverage'] if selection else 'unavailable', 'selection': selection}
