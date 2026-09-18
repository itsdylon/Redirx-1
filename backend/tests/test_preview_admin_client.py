"""Regression guard for internal-preview RLS deployment prerequisites."""
import unittest
import sys
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from src.redirx.database import DeepMatchPreviewDB, SupabaseClient
from backend.services.deep_preview_service import DeepPreviewService
from backend.services.pricing_service import PricingService
from backend.services.stripe_service import StripeService


class PreviewAdminClientTests(unittest.TestCase):
    def test_each_preview_repository_uses_a_fresh_admin_client_not_shared_auth(self):
        first, second = Mock(), Mock()
        with patch.object(SupabaseClient, 'get_admin_client', side_effect=[first, second]) as admin:
            with patch.object(SupabaseClient, 'get_client', side_effect=AssertionError('shared client used')):
                self.assertIs(DeepMatchPreviewDB().client, first)
                self.assertIs(DeepMatchPreviewDB().client, second)
        self.assertEqual(admin.call_count, 2)

    def test_explicit_client_injection_is_preserved_even_if_falsey(self):
        class Client:
            def __bool__(self):
                return False
        client = Client()
        with patch.object(SupabaseClient, 'get_admin_client') as admin:
            self.assertIs(DeepMatchPreviewDB(client=client).client, client)
        admin.assert_not_called()

    def test_preview_service_inherits_fresh_preview_storage_client(self):
        client = Mock()
        with ExitStack() as stack:
            for name in ['MigrationSessionDB', 'URLMappingDB', 'UserQuotaDB', 'WebPageEmbeddingDB']:
                stack.enter_context(patch(f'backend.services.deep_preview_service.{name}'))
            stack.enter_context(patch.object(SupabaseClient, 'get_admin_client', return_value=client))
            stack.enter_context(patch.object(SupabaseClient, 'get_client', side_effect=AssertionError('shared client used')))
            self.assertIs(DeepPreviewService().preview_db.client, client)

    def test_pricing_storage_already_uses_admin_client(self):
        with patch('backend.services.pricing_service.SupabaseClient.get_admin_client') as admin:
            self.assertIs(PricingService().client, admin.return_value)
        admin.assert_called_once_with()

    def test_stripe_storage_and_session_helper_share_fresh_admin_client(self):
        with patch('backend.services.stripe_service.Config.STRIPE_SECRET_KEY', 'sk_test_fixture'):
            with patch('backend.services.stripe_service.SupabaseClient.get_admin_client') as admin:
                with patch('backend.services.stripe_service.PricingService'):
                    with patch('backend.services.stripe_service.stripe'):
                        service = StripeService()
                        self.assertIs(service.client, admin.return_value)
                        self.assertIs(service.session_db.client, admin.return_value)
        admin.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
