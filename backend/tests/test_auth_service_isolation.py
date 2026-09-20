"""Real installed Supabase SDK, intercepted HTTP only; never real auth accounts."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from flask import Flask

from backend.services.auth_service import AuthService, AuthServiceError, Config

A = '10000000-0000-0000-0000-000000000001'
B = '10000000-0000-0000-0000-000000000002'
SERVICE_KEY = 'fixture-service-key'
REAL_HTTP_CLIENT = httpx.Client


def user(identity):
    return {'id': identity, 'aud': 'authenticated', 'role': 'authenticated',
            'email': ('a' if identity == A else 'b') + '@fixture.invalid',
            'app_metadata': {}, 'user_metadata': {}, 'created_at': '2026-01-01T00:00:00Z'}


def session(identity, refreshed=False):
    label = 'a' if identity == A else 'b'
    return {'access_token': 'access-' + label + ('-new' if refreshed else ''),
            'refresh_token': 'refresh-' + label, 'expires_in': 3600,
            'token_type': 'bearer', 'user': user(identity)}


class AuthServiceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.transports = []
        self.revoke = []
        self.profile_error = False
        for name, value in [('SUPABASE_URL', 'https://auth.fixture.invalid'), ('SUPABASE_KEY', SERVICE_KEY)]:
            patcher = patch.object(Config, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        transport_patch = patch('backend.services.auth_service.httpx.Client', side_effect=self.new_http)
        transport_patch.start(); self.addCleanup(transport_patch.stop)
        # A regression to any database singleton is a hard failure, not a
        # silent request to a developer's configured project.
        singleton = patch('backend.services.auth_service.SupabaseClient.get_client',
                          side_effect=AssertionError('Auth must not use the singleton'))
        singleton.start(); self.addCleanup(singleton.stop)
        self.addCleanup(self.close_transports)

    def close_transports(self):
        for client in self.transports:
            client.close()

    def new_http(self, **kwargs):
        client = REAL_HTTP_CLIENT(transport=httpx.MockTransport(self.respond), **kwargs)
        self.transports.append(client)
        return client

    def respond(self, request):
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == '/auth/v1/token':
            if body.get('password') == 'wrong':
                return httpx.Response(400, json={'code': 'invalid_credentials', 'msg': 'Invalid login credentials'})
            refreshed = request.url.params.get('grant_type') == 'refresh_token'
            identity = A if body.get('email', '').startswith('a@') or body.get('refresh_token') == 'refresh-a' else B
            return httpx.Response(200, json=session(identity, refreshed))
        if path == '/auth/v1/signup':
            return httpx.Response(200, json=session(A))
        if path == '/auth/v1/resend':
            return httpx.Response(200, json={})
        if path == '/auth/v1/user':
            jwt = request.headers.get('authorization')
            if jwt not in {'Bearer access-a', 'Bearer access-b', 'Bearer access-a-new'}:
                return httpx.Response(401, json={'msg': 'Invalid token'})
            return httpx.Response(200, json=user(B if jwt == 'Bearer access-b' else A))
        if path == '/auth/v1/logout':
            self.revoke.append((request.headers.get('authorization'), request.url.params.get('scope')))
            return httpx.Response(204)
        if path == '/rest/v1/user_profiles':
            if self.profile_error or request.headers.get('authorization') != 'Bearer ' + SERVICE_KEY:
                return httpx.Response(406, json={'code': 'PGRST116', 'message': 'Profile unavailable', 'details': '', 'hint': ''})
            identity = request.url.params['id'].removeprefix('eq.')
            return httpx.Response(200, json={'id': identity, 'full_name': 'Profile ' + identity, 'plan': 'free'})
        raise AssertionError(f'Unexpected mocked HTTP path: {path}')

    def assert_closed(self):
        self.assertTrue(self.transports)
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_constructor_is_lazy_and_blank_tokens_never_use_ambient_auth(self):
        service = AuthService()
        for value in (None, '', ' '):
            self.assertIsNone(service.verify_token(value))
            service.logout(value)
            with self.assertRaises(AuthServiceError):
                service.refresh_token(value)
        self.assertEqual(self.transports, [])
        self.assertEqual(self.requests, [])

    def test_sdk_positive_control_signin_mutates_headers_even_without_persistence(self):
        client, transport = AuthService._new_owned_client()
        try:
            self.assertFalse(client.auth._auto_refresh_token)
            self.assertFalse(client.auth._persist_session)
            self.assertEqual(client.options.headers['Authorization'], 'Bearer ' + SERVICE_KEY)
            prior_postgrest = client.postgrest
            client.auth.sign_in_with_password({'email': 'a@fixture.invalid', 'password': 'fixture'})
            self.assertEqual(client.options.headers['Authorization'], 'Bearer access-a')
            self.assertIsNot(client.postgrest, prior_postgrest)
            self.assertIsNone(client.auth._refresh_token_timer)
            with self.assertRaises(Exception):
                client.table('user_profiles').select('*').eq('id', B).single().execute()
            self.assertEqual(self.requests[-1].headers['authorization'], 'Bearer access-a')
        finally:
            transport.close()

    def test_interleaved_logins_refresh_and_other_user_profile_keep_database_privileged(self):
        first, second = AuthService(), AuthService()
        self.assertEqual(first.login('a@fixture.invalid', 'fixture')['access_token'], 'access-a')
        self.assertEqual(second.login('b@fixture.invalid', 'fixture')['access_token'], 'access-b')
        self.assertEqual(first.refresh_token('refresh-a')['access_token'], 'access-a-new')
        self.assertEqual(first.verify_token('access-b').id, B)
        self.assertEqual(first.get_user_profile(B)['id'], B)
        self.assertEqual(second.get_user_profile(A)['id'], A)
        profiles = [r for r in self.requests if r.url.path.startswith('/rest/')]
        self.assertEqual([r.headers['authorization'] for r in profiles], ['Bearer ' + SERVICE_KEY] * 2)
        self.assertEqual(len(self.transports), 6)
        self.assertEqual(len({id(t) for t in self.transports}), 6)
        self.assert_closed()

    def test_signup_resend_and_refresh_close_owned_transports(self):
        service = AuthService()
        self.assertFalse(service.register('a@fixture.invalid', 'fixture')['email_confirmation_required'])
        service.resend_confirmation_email('a@fixture.invalid', 'https://app.fixture.invalid/auth/callback')
        service.refresh_token('refresh-a')
        self.assertEqual(len(self.transports), 3)
        self.assert_closed()

    def test_actual_auth_routes_keep_other_user_profile_after_email_login_and_refresh(self):
        from backend.routes.auth_routes import auth_blueprint
        app = Flask(__name__)
        app.register_blueprint(auth_blueprint, url_prefix='/api/auth')
        # The repository supports both module import names. Keep the actual
        # route/decorator/SDK path while replacing only welcome-email work.
        with patch('backend.routes.auth_routes.AuthService', AuthService), \
             patch('services.auth_service.AuthService', AuthService), \
             patch('backend.routes.auth_routes._send_welcome_if_needed'):
            client = app.test_client()
            login = client.post('/api/auth/login', json={'email': 'a@fixture.invalid', 'password': 'fixture'})
            self.assertEqual(login.status_code, 200)
            for refresh in (False, True):
                if refresh:
                    response = client.post('/api/auth/refresh', json={'refresh_token': 'refresh-a'})
                    self.assertEqual(response.status_code, 200)
                me = client.get('/api/auth/me', headers={'Authorization': 'Bearer access-b'})
                self.assertEqual(me.status_code, 200)
                self.assertEqual(me.json['user']['id'], B)
                self.assertEqual(me.json['user']['email'], 'b@fixture.invalid')
            client.post('/api/auth/logout', headers={'Authorization': 'Bearer access-b'})
            self.assertEqual(self.revoke, [('Bearer access-b', 'local')])
        self.assert_closed()

    def test_logout_uses_supplied_session_and_local_scope_not_last_login(self):
        service = AuthService()
        service.login('a@fixture.invalid', 'fixture')
        service.logout('access-b')
        self.assertEqual(self.revoke, [('Bearer access-b', 'local')])
        self.assert_closed()

    def test_failure_closes_owned_auth_and_profile_transports(self):
        with self.assertRaises(AuthServiceError) as error:
            AuthService().login('a@fixture.invalid', 'wrong')
        self.assertEqual(error.exception.code, 'auth_invalid_credentials')
        self.assertIsNone(AuthService().verify_token('invalid-explicit-token'))
        self.profile_error = True
        with self.assertRaises(Exception):
            AuthService().get_user_profile(B)
        self.assert_closed()

    def test_factory_failure_closes_transport(self):
        with patch('backend.services.auth_service.create_client', side_effect=ValueError('fixture initialization failure')):
            with self.assertRaises(ValueError):
                AuthService._new_owned_client()
        self.assert_closed()

    def test_injected_clients_are_compatible_and_caller_owned(self):
        auth = Mock()
        auth.auth.sign_in_with_password.return_value = SimpleNamespace(user=user(A), session=SimpleNamespace(
            access_token='fixture-access', refresh_token='fixture-refresh'))
        db = Mock()
        with AuthService(client=db, auth_client=auth) as service:
            self.assertIs(service.client, db)
            self.assertEqual(service.login('a@fixture.invalid', 'fixture')['access_token'], 'fixture-access')
            service.logout('caller-token')
        auth.auth.admin.sign_out.assert_called_once_with('caller-token', scope='local')
        auth.auth.sign_out.assert_not_called()
        auth.close.assert_not_called(); db.close.assert_not_called()
        AuthService(client=auth).login('a@fixture.invalid', 'fixture')
        self.assertEqual(auth.auth.sign_in_with_password.call_count, 2)
        self.assertEqual(self.transports, [])

    def test_profile_property_closes_after_flask_response_even_on_error(self):
        app = Flask(__name__)
        @app.get('/profile/<identity>')
        def profile(identity):
            service = AuthService()
            # Compatibility callers also write directly through .client.
            service.client.table('user_profiles').update({'full_name': 'Changed'}).eq('id', identity).execute()
            if identity == A:
                raise RuntimeError('fixture route failure')
            return service.get_user_profile(identity)
        client = app.test_client()
        self.assertEqual(client.get('/profile/' + B).json['id'], B)
        self.assert_closed()
        self.assertEqual(client.get('/profile/' + A).status_code, 500)
        self.assert_closed()

    def test_direct_database_property_context_closes_transport(self):
        with AuthService() as service:
            service.client.table('user_profiles').select('*').eq('id', B).single().execute()
            self.assertFalse(self.transports[-1].is_closed)
        self.assert_closed()


if __name__ == '__main__':
    unittest.main()
