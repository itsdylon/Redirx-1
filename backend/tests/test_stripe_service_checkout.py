from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

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
