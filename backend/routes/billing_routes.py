"""
Billing routes for Stripe checkout, portal, and webhook handling.
"""
from flask import Blueprint, request, jsonify
import logging

from backend.services.auth_service import require_auth
from backend.services.stripe_service import StripeService
from backend.routes.error_utils import error_response
from redirx.config import Config

logger = logging.getLogger(__name__)

billing_blueprint = Blueprint('billing', __name__)


def _get_stripe_service() -> StripeService:
    """Lazily create StripeService (only when billing endpoints are called)."""
    return StripeService()


def _map_billing_value_error(exc: Exception):
    message = str(exc).lower()

    if "no stripe customer" in message:
        return (
            "billing_no_customer",
            "No billing account was found for this user.",
            404,
            False,
            "contact_support",
        )

    if "price" in message or "plan" in message:
        return (
            "billing_invalid_price",
            "That billing option is not available.",
            400,
            False,
            "select_plan",
        )

    return (
        "billing_invalid_request",
        "Your billing request could not be processed.",
        400,
        False,
        "retry",
    )


@billing_blueprint.route('/create-checkout-session', methods=['POST'])
@require_auth
def create_checkout_session():
    """
    Create a Stripe Checkout session for purchasing a plan.

    Body: { price_id: string, success_url?: string, cancel_url?: string }
    Returns: { url: string }
    """
    data = request.get_json()

    if not data or not data.get('price_id'):
        return error_response(
            code="billing_price_required",
            user_message="Please select a plan before checkout.",
            status=400,
            retryable=False,
            next_action="select_plan",
        )

    price_id = data['price_id']
    # Default URLs redirect back to settings with status param
    origin = request.headers.get('Origin', 'http://localhost:3000')
    success_url = data.get('success_url', f'{origin}/settings?tab=subscription&status=success')
    cancel_url = data.get('cancel_url', f'{origin}/settings?tab=subscription&status=cancelled')

    try:
        service = _get_stripe_service()
        url, _ = service.create_checkout_session(
            user_id=request.user.id,
            email=request.user.email,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({'url': url})
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Checkout session creation failed: {e}")
        return error_response(
            code="billing_checkout_failed",
            user_message="Unable to start checkout right now. Please try again.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/create-portal-session', methods=['POST'])
@require_auth
def create_portal_session():
    """
    Create a Stripe Customer Portal session for managing subscription.

    Body: { return_url?: string }
    Returns: { url: string }
    """
    data = request.get_json(silent=True) or {}
    origin = request.headers.get('Origin', 'http://localhost:3000')
    return_url = data.get('return_url', f'{origin}/settings?tab=subscription')

    try:
        service = _get_stripe_service()
        url = service.create_portal_session(
            user_id=request.user.id,
            return_url=return_url,
        )
        return jsonify({'url': url})
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Portal session creation failed: {e}")
        return error_response(
            code="billing_portal_failed",
            user_message="Unable to open billing portal right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/webhook', methods=['POST'])
def stripe_webhook():
    """
    Stripe webhook endpoint. No auth required - verified by Stripe signature.
    """
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        return error_response(
            code="billing_webhook_signature_missing",
            user_message="Missing Stripe signature header.",
            status=400,
            retryable=False,
            next_action="contact_support",
        )

    try:
        service = _get_stripe_service()
        result = service.handle_webhook_event(payload, sig_header)
        return jsonify(result)
    except ValueError as e:
        logger.warning(f"Webhook signature verification failed: {e}")
        return error_response(
            code="billing_webhook_signature_invalid",
            user_message="Invalid webhook signature.",
            status=400,
            retryable=False,
            next_action="contact_support",
        )
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}")
        return error_response(
            code="billing_webhook_failed",
            user_message="Webhook processing failed.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/update-subscription', methods=['POST'])
@require_auth
def update_subscription():
    """
    Switch an existing subscription to a different plan/price.

    Body: { price_id: string }
    Returns: { plan: string, status: string }
    """
    data = request.get_json()

    if not data or not data.get('price_id'):
        return error_response(
            code="billing_price_required",
            user_message="Please select a plan before updating your subscription.",
            status=400,
            retryable=False,
            next_action="select_plan",
        )

    try:
        service = _get_stripe_service()
        result = service.update_subscription(
            user_id=request.user.id,
            price_id=data['price_id'],
        )
        return jsonify(result)
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Subscription update failed: {e}")
        return error_response(
            code="billing_update_failed",
            user_message="Unable to update your subscription right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/cancel-subscription', methods=['POST'])
@require_auth
def cancel_subscription():
    """
    Cancel subscription at end of current billing period.

    Returns: { status, cancel_at_period_end, current_period_end }
    """
    try:
        service = _get_stripe_service()
        result = service.cancel_subscription(user_id=request.user.id)
        return jsonify(result)
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Subscription cancellation failed: {e}")
        return error_response(
            code="billing_cancel_failed",
            user_message="Unable to cancel your subscription right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/reactivate-subscription', methods=['POST'])
@require_auth
def reactivate_subscription():
    """
    Undo a pending cancellation, keeping the subscription active.

    Returns: { status, cancel_at_period_end, current_period_end }
    """
    try:
        service = _get_stripe_service()
        result = service.reactivate_subscription(user_id=request.user.id)
        return jsonify(result)
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Subscription reactivation failed: {e}")
        return error_response(
            code="billing_reactivate_failed",
            user_message="Unable to reactivate your subscription right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/plans', methods=['GET'])
def get_plans():
    """
    Get available subscription plans with pricing info.
    Public endpoint - no auth required.
    """
    plans = StripeService.get_available_plans()
    return jsonify({'plans': plans})


@billing_blueprint.route('/subscription', methods=['GET'])
@require_auth
def get_subscription():
    """
    Get current user's subscription status.
    """
    try:
        service = _get_stripe_service()
        status = service.get_subscription_status(request.user.id)
        return jsonify(status)
    except ValueError as e:
        code, message, status, retryable, next_action = _map_billing_value_error(e)
        return error_response(
            code=code,
            user_message=message,
            status=status,
            retryable=retryable,
            next_action=next_action,
        )
    except Exception as e:
        logger.error(f"Failed to get subscription status: {e}")
        return error_response(
            code="billing_subscription_status_failed",
            user_message="Unable to load subscription details right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@billing_blueprint.route('/admin/reconcile', methods=['POST'])
def reconcile_plans():
    """
    Reconcile recent Stripe checkouts against user_profiles.
    Authenticated via X-Cron-Secret header (same pattern as trial expiry).
    Run nightly as a safety net to catch missed/failed webhooks.
    """
    cron_secret = Config.CRON_SECRET
    provided = request.headers.get('X-Cron-Secret', '')

    if not cron_secret or provided != cron_secret:
        return error_response(
            code="billing_admin_unauthorized",
            user_message="Unauthorized.",
            status=401,
            retryable=False,
            next_action="authenticate",
        )

    try:
        service = _get_stripe_service()
        result = service.reconcile_recent_checkouts()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        return error_response(
            code="billing_reconciliation_failed",
            user_message="Reconciliation failed.",
            status=500,
            retryable=True,
            next_action="retry",
        )
