"""
Billing routes for Stripe checkout, portal, and webhook handling.
"""
from flask import Blueprint, request, jsonify
import logging

from backend.services.auth_service import require_auth
from backend.services.stripe_service import StripeService
from redirx.config import Config

logger = logging.getLogger(__name__)

billing_blueprint = Blueprint('billing', __name__)


def _get_stripe_service() -> StripeService:
    """Lazily create StripeService (only when billing endpoints are called)."""
    return StripeService()


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
        return jsonify({'error': 'price_id is required'}), 400

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
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Checkout session creation failed: {e}")
        return jsonify({'error': 'Failed to create checkout session'}), 500


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
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Portal session creation failed: {e}")
        return jsonify({'error': 'Failed to create portal session'}), 500


@billing_blueprint.route('/webhook', methods=['POST'])
def stripe_webhook():
    """
    Stripe webhook endpoint. No auth required - verified by Stripe signature.
    """
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        return jsonify({'error': 'Missing Stripe-Signature header'}), 400

    try:
        service = _get_stripe_service()
        result = service.handle_webhook_event(payload, sig_header)
        return jsonify(result)
    except ValueError as e:
        logger.warning(f"Webhook signature verification failed: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}")
        return jsonify({'error': 'Webhook processing failed'}), 500


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
        return jsonify({'error': 'price_id is required'}), 400

    try:
        service = _get_stripe_service()
        result = service.update_subscription(
            user_id=request.user.id,
            price_id=data['price_id'],
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Subscription update failed: {e}")
        return jsonify({'error': 'Failed to update subscription'}), 500


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
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Subscription cancellation failed: {e}")
        return jsonify({'error': 'Failed to cancel subscription'}), 500


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
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Subscription reactivation failed: {e}")
        return jsonify({'error': 'Failed to reactivate subscription'}), 500


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
    except Exception as e:
        logger.error(f"Failed to get subscription status: {e}")
        return jsonify({'error': 'Failed to get subscription status'}), 500


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
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        service = _get_stripe_service()
        result = service.reconcile_recent_checkouts()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        return jsonify({'error': 'Reconciliation failed'}), 500
