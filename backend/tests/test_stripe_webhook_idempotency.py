import unittest
from unittest.mock import Mock, patch

from backend.services.stripe_service import StripeService


class StripeWebhookIdempotencyTests(unittest.TestCase):
    def _service_without_init(self) -> StripeService:
        service = StripeService.__new__(StripeService)
        service._already_processed_webhook = Mock(return_value=False)
        service._record_webhook = Mock()
        service._handle_checkout_completed = Mock()
        service._handle_subscription_updated = Mock()
        service._handle_subscription_deleted = Mock()
        return service

    @patch("backend.services.stripe_service.Config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    @patch("backend.services.stripe_service.stripe.Webhook.construct_event")
    def test_duplicate_event_returns_duplicate_without_reprocessing(self, construct_event: Mock):
        service = self._service_without_init()
        service._already_processed_webhook.return_value = True
        construct_event.return_value = {
            "id": "evt_123",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_123", "metadata": {"checkout_type": "project"}}},
        }

        result = service.handle_webhook_event(b"{}", "sig")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["event_type"], "checkout.session.completed")
        self.assertTrue(result["duplicate"])
        service._handle_checkout_completed.assert_not_called()
        service._record_webhook.assert_not_called()

    @patch("backend.services.stripe_service.Config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    @patch("backend.services.stripe_service.stripe.Webhook.construct_event")
    def test_new_checkout_event_is_processed_and_recorded(self, construct_event: Mock):
        service = self._service_without_init()
        construct_event.return_value = {
            "id": "evt_456",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_456", "metadata": {"checkout_type": "project"}}},
        }

        result = service.handle_webhook_event(b"{}", "sig")

        self.assertEqual(result, {"status": "ok", "event_type": "checkout.session.completed"})
        service._handle_checkout_completed.assert_called_once_with(
            {"id": "cs_456", "metadata": {"checkout_type": "project"}}
        )
        service._record_webhook.assert_called_once_with("evt_456", "checkout.session.completed")

    @patch("backend.services.stripe_service.Config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    @patch("backend.services.stripe_service.stripe.Webhook.construct_event")
    def test_replayed_event_processes_once_and_then_short_circuits(self, construct_event: Mock):
        service = self._service_without_init()
        service._already_processed_webhook.side_effect = [False, True]
        construct_event.return_value = {
            "id": "evt_789",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_789", "metadata": {"checkout_type": "project"}}},
        }

        first = service.handle_webhook_event(b"{}", "sig")
        second = service.handle_webhook_event(b"{}", "sig")

        self.assertEqual(first, {"status": "ok", "event_type": "checkout.session.completed"})
        self.assertEqual(
            second,
            {"status": "ok", "event_type": "checkout.session.completed", "duplicate": True},
        )
        service._handle_checkout_completed.assert_called_once_with(
            {"id": "cs_789", "metadata": {"checkout_type": "project"}}
        )
        service._record_webhook.assert_called_once_with("evt_789", "checkout.session.completed")

    def test_project_checkout_completion_marks_paid_and_queues_deep_session(self):
        service = StripeService.__new__(StripeService)
        service.pricing_service = Mock()
        service.pricing_service.get_quote_by_id.side_effect = [
            {
                "id": "quote-1",
                "status": "checkout_created",
                "deep_session_id": None,
                "source_session_id": "11111111-1111-1111-1111-111111111111",
            },
            {
                "id": "quote-1",
                "status": "paid",
                "deep_session_id": None,
                "source_session_id": "11111111-1111-1111-1111-111111111111",
            },
        ]
        service.pricing_service.get_quote_by_checkout_session = Mock(return_value=None)
        service.pricing_service.mark_paid = Mock()
        service._queue_deep_session_for_quote = Mock(return_value="deep-session-1")

        service._handle_project_checkout_completion(
            {
                "id": "cs_quoted_1",
                "payment_intent": "pi_1",
                "metadata": {"quote_id": "quote-1"},
            }
        )

        service.pricing_service.mark_paid.assert_called_once_with(
            quote_id="quote-1",
            stripe_payment_intent_id="pi_1",
        )
        service._queue_deep_session_for_quote.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
