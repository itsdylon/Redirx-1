"""
Stripe billing service for Redirx.
Handles checkout sessions, customer portal, and webhook processing.
"""
import stripe
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import sys
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from redirx.config import Config
from redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

# Plan configuration: plan name -> resource limits
# Column mapping: credits_limit, quick_match_limit, max_concurrent_projects
PLAN_CONFIG = {
    'launch': {
        'credits_limit': 0,
        'quick_match_limit': 500,
        'max_concurrent_projects': 1,
    },
    'starter': {
        'credits_limit': 50000,
        'quick_match_limit': None,  # None = unlimited
        'max_concurrent_projects': 1,
    },
    'growth': {
        'credits_limit': 250000,
        'quick_match_limit': None,
        'max_concurrent_projects': 3,
    },
    'scale': {
        'credits_limit': 1000000,
        'quick_match_limit': None,
        'max_concurrent_projects': 10,
    },
    'founder': {
        'credits_limit': 0,
        'quick_match_limit': None,
        'max_concurrent_projects': 3,
        'is_lifetime': True,
        'lifetime_credits_total': 500000,
    },
}

# Plan display info for the /plans endpoint
PLAN_DISPLAY = {
    'launch': {'name': 'Launch', 'monthly_price': 0, 'annual_price': 0, 'description': 'Free forever'},
    'starter': {'name': 'Starter', 'monthly_price': 59, 'annual_price': 590, 'description': 'For small migrations'},
    'growth': {'name': 'Growth', 'monthly_price': 179, 'annual_price': 1790, 'description': 'For growing teams'},
    'scale': {'name': 'Scale', 'monthly_price': 449, 'annual_price': 4490, 'description': 'For large enterprises'},
}

# Sentinel for "unlimited" quick match
UNLIMITED_QUICK_MATCH = 999999999


def _build_price_to_plan_map() -> Dict[str, str]:
    """Build a mapping from Stripe price IDs to plan names."""
    mapping = {}
    price_plan_pairs = [
        (Config.STRIPE_PRICE_ID_STARTER_MONTHLY, 'starter'),
        (Config.STRIPE_PRICE_ID_STARTER_ANNUAL, 'starter'),
        (Config.STRIPE_PRICE_ID_GROWTH_MONTHLY, 'growth'),
        (Config.STRIPE_PRICE_ID_GROWTH_ANNUAL, 'growth'),
        (Config.STRIPE_PRICE_ID_SCALE_MONTHLY, 'scale'),
        (Config.STRIPE_PRICE_ID_SCALE_ANNUAL, 'scale'),
        (Config.STRIPE_PRICE_ID_FOUNDER, 'founder'),
    ]
    for price_id, plan in price_plan_pairs:
        if price_id:
            mapping[price_id] = plan
    return mapping


class StripeService:
    """Handles Stripe billing operations."""

    def __init__(self):
        if not Config.STRIPE_SECRET_KEY:
            raise ValueError("STRIPE_SECRET_KEY is not configured")
        stripe.api_key = Config.STRIPE_SECRET_KEY
        self.client = SupabaseClient.get_admin_client()
        self.price_to_plan = _build_price_to_plan_map()

    def _get_or_create_customer(self, user_id: str, email: str) -> str:
        """
        Get existing Stripe customer ID or create a new one.

        Args:
            user_id: Supabase user UUID.
            email: User email.

        Returns:
            Stripe customer ID.
        """
        result = self.client.table('user_profiles').select(
            'stripe_customer_id'
        ).eq('id', user_id).execute()

        if result.data and result.data[0].get('stripe_customer_id'):
            return result.data[0]['stripe_customer_id']

        # Create new Stripe customer
        customer = stripe.Customer.create(
            email=email,
            metadata={'supabase_user_id': user_id},
        )

        # Save to user_profiles
        self.client.table('user_profiles').update({
            'stripe_customer_id': customer.id,
        }).eq('id', user_id).execute()

        return customer.id

    def create_checkout_session(
        self,
        user_id: str,
        email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        extra_metadata: dict = None,
    ) -> tuple[str, str]:
        """
        Create a Stripe Checkout session and return the URL and session ID.

        Args:
            user_id: Supabase user UUID.
            email: User email.
            price_id: Stripe price ID for the plan.
            success_url: URL to redirect to after successful payment.
            cancel_url: URL to redirect to if checkout is cancelled.
            extra_metadata: Additional metadata to merge into session metadata.

        Returns:
            Tuple of (Stripe Checkout session URL, session ID).
        """
        customer_id = self._get_or_create_customer(user_id, email)

        # Determine checkout mode based on price
        plan = self.price_to_plan.get(price_id)
        is_credits = price_id == Config.STRIPE_PRICE_ID_CREDITS
        is_one_time = (
            price_id == Config.STRIPE_PRICE_ID_FOUNDER
            or is_credits
        )
        mode = 'payment' if is_one_time else 'subscription'

        line_item = {'price': price_id, 'quantity': 1}
        if is_credits:
            line_item['adjustable_quantity'] = {
                'enabled': True,
                'minimum': 1,
                'maximum': 999999,
            }

        metadata = {
            'supabase_user_id': user_id,
            'plan': plan or 'credits',
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        session_params = {
            'customer': customer_id,
            'line_items': [line_item],
            'mode': mode,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'metadata': metadata,
        }

        # For subscriptions, allow promotion codes
        if mode == 'subscription':
            session_params['allow_promotion_codes'] = True

        # Add package details to checkout page for founder plan
        if plan == 'founder':
            session_params['custom_text'] = {
                'submit': {
                    'message': (
                        'Founder Package includes:\n'
                        '- 500,000 lifetime mapping credits\n'
                        '- Unlimited Quick Match\n'
                        '- Up to 3 concurrent projects\n'
                        '- Lifetime access — no recurring fees'
                    ),
                },
            }

        session = stripe.checkout.Session.create(**session_params)
        return session.url, session.id

    def create_portal_session(self, user_id: str, return_url: str) -> str:
        """
        Create a Stripe Customer Portal session for managing subscriptions.

        Args:
            user_id: Supabase user UUID.
            return_url: URL to redirect to when leaving the portal.

        Returns:
            Stripe Customer Portal URL.
        """
        result = self.client.table('user_profiles').select(
            'stripe_customer_id'
        ).eq('id', user_id).execute()

        if not result.data or not result.data[0].get('stripe_customer_id'):
            raise ValueError("No Stripe customer found for this user")

        customer_id = result.data[0]['stripe_customer_id']

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    def handle_webhook_event(self, payload: bytes, sig_header: str) -> Dict:
        """
        Verify and handle a Stripe webhook event.

        Args:
            payload: Raw request body.
            sig_header: Stripe-Signature header value.

        Returns:
            Dict with processing result.
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, Config.STRIPE_WEBHOOK_SECRET,
            )
        except stripe.SignatureVerificationError:
            raise ValueError("Invalid webhook signature")

        event_type = event['type']
        event_id = event.get('id')
        data = event['data']['object']

        logger.info(f"Processing Stripe webhook: {event_type} (event {event_id})")

        # Idempotency check: skip if we already processed this event.
        # Wrapped in try/except so webhooks still work if migration 016
        # (stripe_webhook_events table) hasn't been applied yet.
        if event_id:
            try:
                existing = self.client.table('stripe_webhook_events').select('stripe_event_id').eq(
                    'stripe_event_id', event_id
                ).execute()
                if existing.data:
                    logger.info(f"Skipping duplicate webhook event {event_id}")
                    return {'status': 'ok', 'event_type': event_type, 'duplicate': True}
            except Exception as e:
                logger.warning(f"Idempotency check failed (stripe_webhook_events table may not exist): {e}. "
                             f"Proceeding with webhook processing.")

        try:
            if event_type == 'checkout.session.completed':
                self._handle_checkout_completed(data)
            elif event_type == 'customer.subscription.updated':
                self._handle_subscription_updated(data)
            elif event_type == 'customer.subscription.deleted':
                self._handle_subscription_deleted(data)
            elif event_type == 'invoice.payment_failed':
                logger.warning(f"Payment failed for customer {data.get('customer')}")
            else:
                logger.info(f"Unhandled webhook event type: {event_type}")
        except Exception as e:
            logger.error(f"WEBHOOK HANDLER FAILED for {event_type} (event {event_id}): {e}", exc_info=True)
            raise

        # Record processed event for idempotency
        if event_id:
            try:
                self.client.table('stripe_webhook_events').insert({
                    'stripe_event_id': event_id,
                    'event_type': event_type,
                }).execute()
            except Exception as e:
                # If insert fails (e.g. race condition duplicate), that's fine
                logger.warning(f"Failed to record webhook event {event_id}: {e}")

        return {'status': 'ok', 'event_type': event_type}

    def _handle_checkout_completed(self, session: Dict) -> None:
        """Handle successful checkout session completion."""
        customer_id = session.get('customer')
        user_id = session.get('metadata', {}).get('supabase_user_id')
        plan = session.get('metadata', {}).get('plan')
        mode = session.get('mode')

        logger.info(f"Checkout completed: user_id={user_id}, plan={plan}, mode={mode}, customer={customer_id}")

        if not user_id:
            # Fallback: look up user by stripe_customer_id
            result = self.client.table('user_profiles').select('id').eq(
                'stripe_customer_id', customer_id
            ).execute()
            if not result.data:
                logger.error(f"No user found for Stripe customer {customer_id}")
                return
            user_id = result.data[0]['id']

        if plan == 'credits':
            # Add-on credit purchase - each unit = 1,000 credits at $2/unit
            try:
                line_items = stripe.checkout.Session.list_line_items(session.get('id'))
                units = line_items.data[0].quantity if line_items.data else 1
            except Exception:
                units = 1
            credits = units * 1000
            self._add_lifetime_credits(user_id, credits)
            return

        if plan not in PLAN_CONFIG:
            logger.error(f"Unknown plan in checkout metadata: {plan}")
            return

        config = PLAN_CONFIG[plan]
        updates = {
            'plan': plan,
            'max_concurrent_projects': config['max_concurrent_projects'],
            'plan_started_at': datetime.now(timezone.utc).isoformat(),
        }

        if config.get('quick_match_limit') is not None:
            updates['quick_match_limit'] = config['quick_match_limit']
        else:
            updates['quick_match_limit'] = UNLIMITED_QUICK_MATCH

        if mode == 'subscription':
            subscription_id = session.get('subscription')
            updates['stripe_subscription_id'] = subscription_id
            updates['credits_limit'] = config['credits_limit']
            updates['credits_used'] = 0
            updates['quick_match_used'] = 0
        elif plan == 'founder':
            # One-time founder plan: set lifetime flags
            updates['is_lifetime'] = True
            updates['lifetime_credits_total'] = config.get('lifetime_credits_total', 500000)
            updates['lifetime_credits_used'] = 0

        result = self.client.table('user_profiles').update(updates).eq('id', user_id).execute()
        if not result.data:
            logger.critical(f"PLAN UPDATE FAILED: 0 rows matched for user {user_id} upgrading to {plan}. "
                          f"This likely means auth contamination or missing user_profiles row.")
        else:
            logger.info(f"User {user_id} upgraded to {plan}")

        # If this checkout was initiated via a founder invite, mark the invite as redeemed
        invite_id = session.get('metadata', {}).get('invite_id')
        if invite_id and plan == 'founder':
            try:
                from backend.services.trial_service import TrialService
                trial_service = TrialService()
                # Look up user email for the redemption record
                user_result = self.client.table('user_profiles').select('id').eq('id', user_id).execute()
                email = session.get('customer_details', {}).get('email', '')
                trial_service.redeem_founder_invite(invite_id, user_id, email)
            except Exception as e:
                logger.error(f"Failed to redeem founder invite {invite_id}: {e}")

    def _handle_subscription_updated(self, subscription: Dict) -> None:
        """Handle subscription plan change (upgrade/downgrade)."""
        customer_id = subscription.get('customer')
        subscription_id = subscription.get('id')

        result = self.client.table('user_profiles').select('id').eq(
            'stripe_customer_id', customer_id
        ).execute()

        if not result.data:
            logger.error(f"No user found for customer {customer_id}")
            return

        user_id = result.data[0]['id']

        # Get the current price from the subscription items
        items = subscription.get('items', {}).get('data', [])
        if not items:
            return

        price_id = items[0].get('price', {}).get('id')
        plan = self.price_to_plan.get(price_id)

        if not plan or plan not in PLAN_CONFIG:
            logger.warning(f"Unknown price ID in subscription update: {price_id}")
            return

        config = PLAN_CONFIG[plan]
        updates = {
            'plan': plan,
            'stripe_subscription_id': subscription_id,
            'credits_limit': config['credits_limit'],
            'max_concurrent_projects': config['max_concurrent_projects'],
        }

        if config.get('quick_match_limit') is not None:
            updates['quick_match_limit'] = config['quick_match_limit']
        else:
            updates['quick_match_limit'] = UNLIMITED_QUICK_MATCH

        result = self.client.table('user_profiles').update(updates).eq('id', user_id).execute()
        if not result.data:
            logger.critical(f"SUBSCRIPTION UPDATE FAILED: 0 rows matched for user {user_id} updating to {plan}")
        else:
            logger.info(f"User {user_id} subscription updated to {plan}")

    def _handle_subscription_deleted(self, subscription: Dict) -> None:
        """Handle subscription cancellation - downgrade to launch."""
        customer_id = subscription.get('customer')

        result = self.client.table('user_profiles').select('id').eq(
            'stripe_customer_id', customer_id
        ).execute()

        if not result.data:
            logger.error(f"No user found for customer {customer_id}")
            return

        user_id = result.data[0]['id']
        launch_config = PLAN_CONFIG['launch']

        result = self.client.table('user_profiles').update({
            'plan': 'launch',
            'stripe_subscription_id': None,
            'credits_limit': launch_config['credits_limit'],
            'credits_used': 0,
            'quick_match_limit': launch_config['quick_match_limit'],
            'quick_match_used': 0,
            'max_concurrent_projects': launch_config['max_concurrent_projects'],
        }).eq('id', user_id).execute()

        if not result.data:
            logger.critical(f"DOWNGRADE FAILED: 0 rows matched for user {user_id} downgrading to launch")
        else:
            logger.info(f"User {user_id} downgraded to launch (subscription cancelled)")

    def _add_lifetime_credits(self, user_id: str, amount: int) -> None:
        """Add lifetime credits to a user (from credit pack purchase)."""
        result = self.client.table('user_profiles').select(
            'lifetime_credits_total, is_lifetime'
        ).eq('id', user_id).execute()

        current_total = 0
        if result.data:
            current_total = result.data[0].get('lifetime_credits_total') or 0

        result = self.client.table('user_profiles').update({
            'is_lifetime': True,
            'lifetime_credits_total': current_total + amount,
        }).eq('id', user_id).execute()

        if not result.data:
            logger.critical(f"CREDIT ADD FAILED: 0 rows matched for user {user_id} adding {amount} credits")
        else:
            logger.info(f"Added {amount} lifetime credits to user {user_id}")

    def update_subscription(self, user_id: str, price_id: str) -> Dict:
        """
        Change an existing subscription to a different price (plan swap with proration).

        Args:
            user_id: Supabase user UUID.
            price_id: New Stripe price ID to switch to.

        Returns:
            Dict with updated plan info.
        """
        result = self.client.table('user_profiles').select(
            'stripe_subscription_id'
        ).eq('id', user_id).execute()

        if not result.data or not result.data[0].get('stripe_subscription_id'):
            raise ValueError("No active subscription found for this user")

        subscription_id = result.data[0]['stripe_subscription_id']

        # Retrieve the subscription to get the current item ID
        sub = stripe.Subscription.retrieve(subscription_id)
        if not sub['items']['data']:
            raise ValueError("Subscription has no items")

        item_id = sub['items']['data'][0]['id']

        # Modify subscription: swap price with proration
        updated_sub = stripe.Subscription.modify(
            subscription_id,
            items=[{'id': item_id, 'price': price_id}],
            proration_behavior='create_prorations',
        )

        # Determine the new plan from the price ID
        plan = self.price_to_plan.get(price_id)
        if plan and plan in PLAN_CONFIG:
            config = PLAN_CONFIG[plan]
            updates = {
                'plan': plan,
                'credits_limit': config['credits_limit'],
                'max_concurrent_projects': config['max_concurrent_projects'],
            }
            if config.get('quick_match_limit') is not None:
                updates['quick_match_limit'] = config['quick_match_limit']
            else:
                updates['quick_match_limit'] = UNLIMITED_QUICK_MATCH

            self.client.table('user_profiles').update(updates).eq('id', user_id).execute()

        logger.info(f"User {user_id} switched subscription to {plan}")
        return {'plan': plan, 'status': 'updated'}

    def cancel_subscription(self, user_id: str) -> Dict:
        """
        Cancel a subscription at the end of the current billing period.

        Args:
            user_id: Supabase user UUID.

        Returns:
            Dict with cancellation info.
        """
        result = self.client.table('user_profiles').select(
            'stripe_subscription_id'
        ).eq('id', user_id).execute()

        if not result.data or not result.data[0].get('stripe_subscription_id'):
            raise ValueError("No active subscription found for this user")

        subscription_id = result.data[0]['stripe_subscription_id']

        updated_sub = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=True,
        )

        logger.info(f"User {user_id} scheduled subscription cancellation")
        return {
            'status': 'cancelling',
            'cancel_at_period_end': True,
            'current_period_end': updated_sub.get('current_period_end'),
        }

    def reactivate_subscription(self, user_id: str) -> Dict:
        """
        Undo a pending cancellation by setting cancel_at_period_end back to False.

        Args:
            user_id: Supabase user UUID.

        Returns:
            Dict with reactivation info.
        """
        result = self.client.table('user_profiles').select(
            'stripe_subscription_id'
        ).eq('id', user_id).execute()

        if not result.data or not result.data[0].get('stripe_subscription_id'):
            raise ValueError("No active subscription found for this user")

        subscription_id = result.data[0]['stripe_subscription_id']

        # Clear both cancel_at_period_end and cancel_at (Stripe may use either)
        updated_sub = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=False,
            cancel_at='',
        )

        logger.info(f"User {user_id} reactivated subscription")
        return {
            'status': 'active',
            'cancel_at_period_end': False,
            'cancel_at': None,
            'current_period_end': updated_sub.get('current_period_end'),
        }

    def get_subscription_status(self, user_id: str) -> Dict:
        """
        Get the current subscription status for a user.

        Returns:
            Dict with plan details, credits, and limits.
        """
        result = self.client.table('user_profiles').select(
            'plan, stripe_subscription_id, '
            'credits_limit, credits_used, '
            'is_lifetime, lifetime_credits_total, lifetime_credits_used, '
            'quick_match_limit, quick_match_used, '
            'max_concurrent_projects, trial_expires_at'
        ).eq('id', user_id).execute()

        if not result.data:
            return {
                'plan': 'launch',
                'has_subscription': False,
                'credits_limit': 0,
                'credits_used': 0,
                'credits_remaining': 0,
                'is_lifetime': False,
                'lifetime_credits_total': 0,
                'lifetime_credits_used': 0,
                'lifetime_credits_remaining': 0,
                'quick_match_limit': 500,
                'quick_match_used': 0,
                'quick_match_unlimited': False,
                'max_concurrent_projects': 1,
                'current_period_end': None,
                'cancel_at_period_end': False,
            }

        profile = result.data[0]
        plan = profile.get('plan') or 'launch'
        credits_limit = profile.get('credits_limit') or 0
        credits_used = profile.get('credits_used') or 0
        is_lifetime = profile.get('is_lifetime') or False
        lifetime_total = profile.get('lifetime_credits_total') or 0
        lifetime_used = profile.get('lifetime_credits_used') or 0
        quick_limit = profile.get('quick_match_limit') or 500
        quick_used = profile.get('quick_match_used') or 0

        # Fetch billing period info from Stripe if user has an active subscription
        current_period_end = None
        cancel_at_period_end = False
        cancel_at = None
        stripe_sub_id = profile.get('stripe_subscription_id')
        if stripe_sub_id:
            try:
                sub = stripe.Subscription.retrieve(stripe_sub_id)
                current_period_end = sub.get('current_period_end')
                cancel_at_period_end = sub.get('cancel_at_period_end', False)
                cancel_at = sub.get('cancel_at')
            except Exception as e:
                logger.warning(f"Failed to fetch Stripe subscription {stripe_sub_id}: {e}")

        return {
            'plan': plan,
            'has_subscription': bool(stripe_sub_id),
            'credits_limit': credits_limit,
            'credits_used': credits_used,
            'credits_remaining': max(0, credits_limit - credits_used),
            'is_lifetime': is_lifetime,
            'lifetime_credits_total': lifetime_total,
            'lifetime_credits_used': lifetime_used,
            'lifetime_credits_remaining': max(0, lifetime_total - lifetime_used) if is_lifetime else 0,
            'quick_match_limit': quick_limit,
            'quick_match_used': quick_used,
            'quick_match_unlimited': quick_limit >= UNLIMITED_QUICK_MATCH,
            'max_concurrent_projects': profile.get('max_concurrent_projects') or 1,
            'current_period_end': current_period_end,
            'cancel_at_period_end': cancel_at_period_end,
            'cancel_at': cancel_at,
            'trial_expires_at': profile.get('trial_expires_at'),
        }

    @staticmethod
    def get_available_plans() -> list:
        """Return available plans with pricing info and price IDs."""
        plans = []
        for plan_key, display in PLAN_DISPLAY.items():
            config = PLAN_CONFIG[plan_key]
            plan_info = {
                'id': plan_key,
                'name': display['name'],
                'description': display['description'],
                'monthly_price': display['monthly_price'],
                'annual_price': display.get('annual_price', 0),
                'credits_limit': config.get('credits_limit', 0),
                'lifetime_credits_total': config.get('lifetime_credits_total', 0),
                'quick_match_limit': config.get('quick_match_limit'),
                'max_concurrent_projects': config['max_concurrent_projects'],
            }

            # Attach price IDs if configured
            if plan_key == 'starter':
                plan_info['price_id_monthly'] = Config.STRIPE_PRICE_ID_STARTER_MONTHLY
                plan_info['price_id_annual'] = Config.STRIPE_PRICE_ID_STARTER_ANNUAL
            elif plan_key == 'growth':
                plan_info['price_id_monthly'] = Config.STRIPE_PRICE_ID_GROWTH_MONTHLY
                plan_info['price_id_annual'] = Config.STRIPE_PRICE_ID_GROWTH_ANNUAL
            elif plan_key == 'scale':
                plan_info['price_id_monthly'] = Config.STRIPE_PRICE_ID_SCALE_MONTHLY
                plan_info['price_id_annual'] = Config.STRIPE_PRICE_ID_SCALE_ANNUAL

            plans.append(plan_info)

        # Include credit pack price ID (available to all plans)
        credits_price_id = Config.STRIPE_PRICE_ID_CREDITS
        if credits_price_id:
            for p in plans:
                p['credits_price_id'] = credits_price_id

        # Include founder price ID as a top-level field on the response
        founder_price_id = Config.STRIPE_PRICE_ID_FOUNDER
        if founder_price_id:
            for p in plans:
                p['founder_price_id'] = founder_price_id

        return plans

    def reconcile_recent_checkouts(self, lookback_hours: int = 24) -> Dict:
        """
        Compare recent Stripe checkout sessions against user_profiles and fix mismatches.
        Intended to be called by a nightly cron as a safety net.

        Returns:
            Dict with reconciliation results.
        """
        import time

        created_after = int(time.time()) - (lookback_hours * 3600)
        fixed = []
        errors = []

        try:
            sessions = stripe.checkout.Session.list(
                limit=100,
                created={'gte': created_after},
            )
        except Exception as e:
            logger.error(f"Reconciliation: failed to list Stripe sessions: {e}")
            return {'fixed': [], 'errors': [str(e)]}

        for session in sessions.auto_paging_iter():
            if session.get('payment_status') != 'paid':
                continue

            user_id = session.get('metadata', {}).get('supabase_user_id')
            expected_plan = session.get('metadata', {}).get('plan')

            if not user_id or not expected_plan or expected_plan not in PLAN_CONFIG:
                continue

            # Skip credit purchases — they add credits, not change plan
            if expected_plan == 'credits':
                continue

            try:
                result = self.client.table('user_profiles').select('plan').eq(
                    'id', user_id
                ).execute()

                if not result.data:
                    errors.append(f"User {user_id} not found in user_profiles")
                    continue

                actual_plan = result.data[0].get('plan')
                if actual_plan != expected_plan:
                    logger.warning(
                        f"Reconciliation: user {user_id} has plan '{actual_plan}' "
                        f"but Stripe shows '{expected_plan}'. Fixing."
                    )
                    # Re-run the checkout handler to apply the correct plan
                    self._handle_checkout_completed(session)
                    fixed.append({
                        'user_id': user_id,
                        'was': actual_plan,
                        'now': expected_plan,
                        'session_id': session.get('id'),
                    })
            except Exception as e:
                errors.append(f"Error checking user {user_id}: {str(e)}")

        logger.info(f"Reconciliation complete: {len(fixed)} fixed, {len(errors)} errors")
        return {'fixed': fixed, 'errors': errors}
