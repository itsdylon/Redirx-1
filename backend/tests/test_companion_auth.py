from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from flask import Flask, request

from backend.routes.v2_routes import authenticated, resolve_authorization
from backend.services.companion_auth_service import resolve_companion_session

A = '10000000-0000-0000-0000-000000000001'


class CompanionAuthTests(TestCase):
    def test_remote_verification_is_authority_and_uses_dedicated_client(self):
        client = Mock()
        with patch('backend.services.companion_auth_service.SupabaseClient.get_admin_client', return_value=client), \
             patch('backend.services.companion_auth_service.AuthService') as auth:
            auth.return_value.verify_token.return_value = SimpleNamespace(id=A)
            self.assertEqual(resolve_companion_session('untrusted.claims.signature'), A)
            auth.assert_called_once_with(client=client)
            auth.return_value.verify_token.assert_called_once_with('untrusted.claims.signature')
            for result in (None, SimpleNamespace(id='invalid'), {'id': A}):
                auth.return_value.verify_token.return_value = result
                self.assertIsNone(resolve_companion_session('untrusted.claims.signature'))
            auth.return_value.verify_token.side_effect = RuntimeError('provider unavailable')
            self.assertIsNone(resolve_companion_session('untrusted.claims.signature'))

    def test_non_jwt_and_oversized_tokens_do_not_call_provider(self):
        with patch('backend.services.companion_auth_service.SupabaseClient.get_admin_client') as client:
            for value in (None, '', 'rdx_invalid', 'opaque', 'a.' + 'b'*16384 + '.c'):
                self.assertIsNone(resolve_companion_session(value))
            client.assert_not_called()

    def test_v2_caches_verified_subject_and_has_no_ambient_cookie_authority(self):
        app = Flask(__name__)
        @app.get('/probe')
        @authenticated
        def probe():
            return {'user_id': request.api_user_id, 'repeat': resolve_authorization()}
        with patch('backend.routes.v2_routes.MCPDelegationService.resolve', return_value=None), \
             patch('backend.routes.v2_routes.resolve_companion_session', return_value=A) as browser:
            client = app.test_client()
            client.set_cookie('access_token', 'fake.jwt.signature')
            self.assertEqual(client.get('/probe?access_token=fake.jwt.signature').status_code, 401)
            browser.assert_not_called()
            response = client.get('/probe', headers={'Authorization': 'Bearer fake.jwt.signature'})
            self.assertEqual(response.json, {'user_id': A, 'repeat': A})
            browser.assert_called_once_with('fake.jwt.signature')

    def test_keys_and_verified_delegations_do_not_fall_through_to_browser_auth(self):
        app = Flask(__name__)
        with patch('backend.routes.v2_routes.ApiKeyService.resolve', return_value=None), \
             patch('backend.routes.v2_routes.MCPDelegationService.resolve', return_value=A), \
             patch('backend.routes.v2_routes.resolve_companion_session') as browser:
            with app.test_request_context(headers={'Authorization': 'Bearer rdx_invalid'}):
                self.assertIsNone(resolve_authorization())
            with app.test_request_context(headers={'Authorization': 'Bearer delegated.token.signature'}):
                self.assertEqual(resolve_authorization(), A)
            browser.assert_not_called()
