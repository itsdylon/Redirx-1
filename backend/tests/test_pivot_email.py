"""Render real transactional templates; provider and storage are isolated."""
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from backend.services.email_service import EmailService, Config


class PivotEmailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'true'})
        self.env.start(); self.addCleanup(self.env.stop)
        key = patch.object(Config, 'RESEND_API_KEY', 'fixture-only')
        key.start(); self.addCleanup(key.stop)
        self.service = EmailService()
        self.service._app_url = 'https://app.fixture.invalid'
        for name in ('_is_opted_out', '_log_send'):
            mocked = patch.object(self.service, name, return_value=False)
            mocked.start(); self.addCleanup(mocked.stop)
        send = patch('backend.services.email_service.resend.Emails.send', return_value={'id':'fixture-email'})
        self.send = send.start(); self.addCleanup(send.stop)

    def test_welcome_connects_agent_and_old_nudges_are_disabled(self):
        self.assertEqual(self.service.send_welcome('owner','owner@fixture.invalid','<script>'), 'fixture-email')
        html = self.send.call_args.args[0]['html']
        self.assertIn('/companion', html)
        self.assertIn('Connect RedirX to your agent', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('/dashboard', html)
        self.send.reset_mock()
        for day in (1,3,7): self.assertIsNone(self.service.send_nudge('owner','owner@fixture.invalid',day))
        self.send.assert_not_called()

    def test_results_link_durable_migration_and_do_not_claim_installation(self):
        self.service.send_mapping_complete('owner','owner@fixture.invalid','Project',274,'legacy',migration_id='durable')
        payload = self.send.call_args.args[0]
        self.assertIn('/migrations/durable', payload['html'])
        self.assertIn('not installed or verified', payload['html'])
        self.assertTrue(payload['subject'].startswith('Matching complete'))
        self.service.send_mapping_failed('owner','owner@fixture.invalid','Project',error_summary='provider-secret',session_id='legacy')
        html = self.send.call_args.args[0]['html']
        self.assertIn('/review/legacy', html)
        self.assertNotIn('provider-secret', html)
        self.assertNotIn('/dashboard', html)

    def test_flag_off_keeps_legacy_welcome_and_nudges(self):
        with patch.dict(os.environ, {'MCP_PIVOT_ENABLED':'false'}):
            self.service.send_welcome('owner','owner@fixture.invalid')
            self.assertIn('/dashboard', self.send.call_args.args[0]['html'])
            self.service.send_nudge('owner','owner@fixture.invalid',1)
            self.assertIn('upload your first CSV', self.send.call_args.args[0]['subject'])


if __name__ == '__main__': unittest.main()
