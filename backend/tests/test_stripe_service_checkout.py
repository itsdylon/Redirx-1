from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import stripe

from backend.services.stripe_service import StripeService


@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_MONTHLY", "price_monthly")
@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_ANNUAL", "price_annual")
@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_OVERAGE", "price_overage")
@patch("backend.services.stripe_service.stripe.checkout.Session.create")
def test_agency_checkout_attaches_base_and_overage_line_items(create_checkout: Mock):
    service = StripeService.__new__(StripeService)
    service._get_or_create_customer = Mock(return_value="cus_123")
    create_checkout.return_value = SimpleNamespace(
        url="https://checkout.stripe.test/agency",
        id="cs_test_agency",
    )

    url, session_id = service.create_agency_checkout_session(
        user_id="user-1",
        email="user@example.com",
        billing_cycle="monthly",
        success_url="https://app.example.com/success",
        cancel_url="https://app.example.com/cancel",
    )

    assert url == "https://checkout.stripe.test/agency"
    assert session_id == "cs_test_agency"

    kwargs = create_checkout.call_args.kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"] == [
        {"price": "price_monthly", "quantity": 1},
        {"price": "price_overage"},
    ]


@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_MONTHLY", "price_monthly")
@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_ANNUAL", "price_annual")
@patch("backend.services.stripe_service.Config.STRIPE_PRICE_ID_AGENCY_OVERAGE", None)
def test_agency_checkout_requires_overage_price_id():
    service = StripeService.__new__(StripeService)
    service._get_or_create_customer = Mock(return_value="cus_123")

    with pytest.raises(ValueError, match="Agency overage price is not configured"):
        service.create_agency_checkout_session(
            user_id="user-1",
            email="user@example.com",
            billing_cycle="monthly",
            success_url="https://app.example.com/success",
            cancel_url="https://app.example.com/cancel",
        )


@patch("backend.services.stripe_service.stripe.Customer.create")
@patch("backend.services.stripe_service.stripe.Customer.retrieve")
def test_get_or_create_customer_reuses_valid_customer_id(retrieve_customer: Mock, create_customer: Mock):
    service = StripeService.__new__(StripeService)
    select_table = Mock()
    select_table.select.return_value = select_table
    select_table.eq.return_value = select_table
    select_table.maybe_single.return_value = select_table
    select_table.execute.return_value = SimpleNamespace(
        data={"stripe_customer_id": "cus_existing"}
    )
    service.client = Mock()
    service.client.table.return_value = select_table
    retrieve_customer.return_value = SimpleNamespace(id="cus_existing", deleted=False)

    customer_id = service._get_or_create_customer("user-1", "user@example.com")

    assert customer_id == "cus_existing"
    retrieve_customer.assert_called_once_with("cus_existing")
    create_customer.assert_not_called()


@patch("backend.services.stripe_service.stripe.Customer.create")
@patch("backend.services.stripe_service.stripe.Customer.retrieve")
def test_get_or_create_customer_recreates_missing_customer_id(retrieve_customer: Mock, create_customer: Mock):
    service = StripeService.__new__(StripeService)

    select_table = Mock()
    select_table.select.return_value = select_table
    select_table.eq.return_value = select_table
    select_table.maybe_single.return_value = select_table
    select_table.execute.return_value = SimpleNamespace(
        data={"stripe_customer_id": "cus_stale"}
    )

    clear_table = Mock()
    clear_table.update.return_value = clear_table
    clear_table.eq.return_value = clear_table
    clear_table.execute.return_value = SimpleNamespace(data=[{}])

    persist_table = Mock()
    persist_table.update.return_value = persist_table
    persist_table.eq.return_value = persist_table
    persist_table.execute.return_value = SimpleNamespace(data=[{}])

    service.client = Mock()
    service.client.table.side_effect = [select_table, clear_table, persist_table]

    retrieve_customer.side_effect = stripe.error.InvalidRequestError(
        "No such customer: 'cus_stale'",
        "customer",
    )
    create_customer.return_value = SimpleNamespace(id="cus_new")

    customer_id = service._get_or_create_customer("user-1", "user@example.com")

    assert customer_id == "cus_new"
    retrieve_customer.assert_called_once_with("cus_stale")
    create_customer.assert_called_once()
    assert service.client.table.call_count == 3


@patch("backend.services.stripe_service.stripe.checkout.Session.retrieve")
def test_reconcile_project_quote_marks_paid_and_queues_deep_session(retrieve_checkout_session: Mock):
    service = StripeService.__new__(StripeService)
    service.pricing_service = Mock()
    service._queue_deep_session_for_quote = Mock(return_value="deep-session-1")

    service.pricing_service.get_quote_for_source.return_value = {
        "id": "quote-1",
        "status": "checkout_created",
        "stripe_checkout_session_id": "cs_test_123",
        "source_session_id": "source-1",
        "deep_session_id": None,
    }
    service.pricing_service.get_quote_by_id.side_effect = [
        {
            "id": "quote-1",
            "status": "paid",
            "stripe_checkout_session_id": "cs_test_123",
            "source_session_id": "source-1",
            "deep_session_id": None,
        },
        {
            "id": "quote-1",
            "status": "paid",
            "stripe_checkout_session_id": "cs_test_123",
            "source_session_id": "source-1",
            "deep_session_id": "deep-session-1",
        },
    ]
    retrieve_checkout_session.return_value = {
        "id": "cs_test_123",
        "status": "complete",
        "payment_status": "paid",
        "payment_intent": "pi_test_123",
    }

    reconciled = service.reconcile_project_quote_for_source(
        source_session_id="source-1",
        user_id="user-1",
    )

    service.pricing_service.mark_paid.assert_called_once_with(
        quote_id="quote-1",
        stripe_payment_intent_id="pi_test_123",
    )
    service._queue_deep_session_for_quote.assert_called_once()
    assert reconciled["status"] == "paid"
    assert reconciled["deep_session_id"] == "deep-session-1"
