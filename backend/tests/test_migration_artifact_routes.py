import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from backend.routes.migration_artifact_routes import create_migration_artifact_blueprint
from backend.services.migration_repository import MigrationNotFoundError

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


class ArtifactRouteTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        app = Flask(__name__)
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        app.register_blueprint(create_migration_artifact_blueprint(lambda: self.service), url_prefix='/api/v2')
        self.client = app.test_client()
        self.flags = patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'true'})
        self.flags.start(); self.addCleanup(self.flags.stop)
        self.auth = patch('backend.routes.v2_routes.MCPDelegationService.resolve', return_value=A)
        self.auth.start(); self.addCleanup(self.auth.stop)
        self.headers = {'Authorization': 'Bearer fixture-delegation'}
        self.path = f'/api/v2/migrations/{A}/artifacts'

    def test_creation_returns_bounded_handoff_and_explicit_partial_policy(self):
        self.service.create_artifact.return_value = {'id': B, 'format': 'nginx', 'verification_inputs': {'large': []},
            'storage_key': 'private', 'content_hash': 'a'*64, 'included_count': 2, 'excluded_count': 1}
        body = {'run_id': B, 'format': 'nginx', 'revision': 3, 'allow_partial': True, 'idempotency_key': 'same'}
        result = self.client.post(self.path, json=body, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['data']['resource_uri'], f'redirx://migrations/{A}/artifacts/{B}')
        self.assertNotIn('verification_inputs', result.json['data'])
        self.assertNotIn('storage_key', result.json['data'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.service.create_artifact.assert_called_once_with(A, A, B, fmt='nginx', selection_revision='3',
            partial_policy='allow', idempotency_key='same')
        for change in ({'revision': True}, {'allow_partial': 'true'}, {'paid': True}):
            self.assertEqual(self.client.post(self.path, json={**body, **change}, headers=self.headers).status_code, 400)
        self.assertEqual(self.service.create_artifact.call_count, 1)

    def test_download_is_owned_no_store_and_does_not_trust_paid_query(self):
        self.service.authorize_download.return_value = {'content': 'fixture redirects', 'artifact_id': B}
        response = self.client.get(self.path+'/'+B, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.service.authorize_download.assert_called_once_with(A, A, B)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get(self.path+'/'+B+'?paid=true', headers=self.headers).status_code, 400)
        self.service.authorize_download.side_effect = MigrationNotFoundError('Artifact not found.')
        self.assertEqual(self.client.get(self.path+'/'+B, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(self.path+'/'+B).status_code, 401)

    def test_master_disabled_has_no_authority_or_storage_calls(self):
        with patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'false'}):
            self.assertEqual(self.client.get(self.path+'/'+B, headers=self.headers).status_code, 503)
        self.service.authorize_download.assert_not_called()
