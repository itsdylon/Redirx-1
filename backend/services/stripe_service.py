"""
Stripe billing service for RedirX pricing v2.
Handles project checkout, agency subscriptions, webhook processing, and metered overages.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import stripe

import sys
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from redirx.config import Config
from redirx.database import SupabaseClient, MigrationSessionDB
from backend.services.pricing_service import PricingService, PRICING_VERSION


logger = logging.getLogger(__name__)

AGENCY_ACTIVE_STATUSES = {
    "active",
    "trialing",
    "past_due",
    "unpaid",
    "incomplete",
}

AGENCY_CANCELLED_STATUSES = {
    "canceled",
    "incomplete_expired",
    "paused",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unix_to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


class StripeService:
    """Handles Stripe billing operations for pricing v2."""

    def __init__(self):
        if not Config.STRIPE_SECRET_KEY:
            raise ValueError("STRIPE_SECRET_KEY is not configured")

        stripe.api_key = Config.STRIPE_SECRET_KEY
        self.client = SupabaseClient.get_admin_client()
        self.pricing_service = PricingService()
        self.session_db = MigrationSessionDB(client=self.client)

    # ---------------------------------------------------------------------
    # Customer helpers
    # ---------------------------------------------------------------------

    def _get_or_create_customer(self, user_id: str, email: str) -> str:
        result = self.client.table("user_profiles").select(
            "stripe_customer_id"
        ).eq("id", user_id).maybe_single().execute()

        if result and result.data and result.data.get("stripe_customer_id"):
            return str(result.data["stripe_customer_id"])

        customer = stripe.Customer.create(
            email=email,
            metadata={"supabase_user_id": user_id},
        )

        self.client.table("user_profiles").update(
            {
                "stripe_customer_id": customer.id,
                "updated_at": _now_iso(),
            }
        ).eq("id", user_id).execute()

        return str(customer.id)

    def _resolve_user_id_from_customer(self, customer_id: str) -> Optional[str]:
        if not customer_id:
            return None

        result = self.client.table("user_profiles").select("id").eq(
            "stripe_customer_id", customer_id
        ).maybe_single().execute()

        if not result or not result.data:
            return None

        return str(result.data["id"])

    @staticmethod
    def _extract_overage_item_id(subscription: Dict[str, Any]) -> Optional[str]:
        items = subscription.get("items", {}).get("data", [])
        for item in items:
            price = item.get("price", {}) if isinstance(item, dict) else {}
            price_id = price.get("id")
            recurring = price.get("recurring", {}) if isinstance(price, dict) else {}
            usage_type = recurring.get("usage_type") if isinstance(recurring, dict) else None

            if Config.STRIPE_PRICE_ID_AGENCY_OVERAGE and price_id == Config.STRIPE_PRICE_ID_AGENCY_OVERAGE:
                return item.get("id")

            if usage_type == "metered":
                return item.get("id")

        return None

    # ---------------------------------------------------------------------
    # Checkout sessions
    # ---------------------------------------------------------------------

    def create_project_checkout_session(
        self,
        *,
        user_id: str,
        email: str,
        quote: Dict[str, Any],
        success_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Create one-time checkout for a Deep Match project quote."""
        if quote.get("user_id") != user_id:
            raise ValueError("Quote does not belong to user")

        status = (quote.get("status") or "").lower()
        if status == "contact_required":
            raise ValueError("Contact required for this quote")
        if status == "paid":
            raise ValueError("Quote already paid")

        amount_cents = int(quote.get("subtotal_cents") or 0)
        billable_pages = int(quote.get("billable_pages") or 0)
        currency = str(quote.get("currency") or "usd")

        if amount_cents <= 0 or billable_pages <= 0:
            raise ValueError("Quote is missing billable amount")

        customer_id = self._get_or_create_customer(user_id, email)

        metadata = {
            "checkout_type": "project",
            "supabase_user_id": user_id,
            "source_session_id": str(quote.get("source_session_id")),
            "quote_id": str(quote.get("id")),
            "pricing_version": str(quote.get("pricing_version") or PRICING_VERSION),
            "billable_pages": str(billable_pages),
        }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": f"RedirX Deep Match Project ({billable_pages:,} pages)",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata=metadata,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return str(session.url), str(session.id)

    def create_agency_checkout_session(
        self,
        *,
        user_id: str,
        email: str,
        billing_cycle: str,
        success_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Create subscription checkout for Agency plan."""
        cycle = (billing_cycle or "monthly").strip().lower()
        if cycle not in {"monthly", "annual"}:
            raise ValueError("Invalid agency billing cycle")

        price_id = (
            Config.STRIPE_PRICE_ID_AGENCY_MONTHLY
            if cycle == "monthly"
            else Config.STRIPE_PRICE_ID_AGENCY_ANNUAL
        )
        if not price_id:
            raise ValueError(f"Agency {cycle} price is not configured")

        customer_id = self._get_or_create_customer(user_id, email)
        metadata = {
            "checkout_type": "agency",
            "supabase_user_id": user_id,
            "billing_cycle": cycle,
        }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            allow_promotion_codes=True,
            metadata=metadata,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return str(session.url), str(session.id)

    def create_portal_session(self, user_id: str, return_url: str) -> str:
        result = self.client.table("user_profiles").select(
            "stripe_customer_id"
        ).eq("id", user_id).maybe_single().execute()

        if not result or not result.data or not result.data.get("stripe_customer_id"):
            raise ValueError("No Stripe customer found for this user")

        session = stripe.billing_portal.Session.create(
            customer=result.data["stripe_customer_id"],
            return_url=return_url,
        )
        return str(session.url)

    # ---------------------------------------------------------------------
    # Webhooks
    # ---------------------------------------------------------------------

    def _already_processed_webhook(self, event_id: str) -> bool:
        if not event_id:
            return False

        try:
            existing = self.client.table("stripe_webhook_events").select("stripe_event_id").eq(
                "stripe_event_id", event_id
            ).maybe_single().execute()
            return bool(existing and existing.data)
        except Exception:
            # If table is unavailable, continue processing.
            return False

    def _record_webhook(self, event_id: str, event_type: str) -> None:
        if not event_id:
            return

        try:
            self.client.table("stripe_webhook_events").insert(
                {
                    "stripe_event_id": event_id,
                    "event_type": event_type,
                }
            ).execute()
        except Exception as e:
            logger.warning("Failed to record webhook event %s: %s", event_id, e)

    def _queue_deep_session_for_quote(self, quote: Dict[str, Any]) -> Optional[str]:
        if quote.get("deep_session_id"):
            return str(quote["deep_session_id"])

        source_session_id = str(quote.get("source_session_id"))
        source_result = self.client.table("migration_sessions").select(
            "id,user_id,project_name,old_urls,new_urls"
        ).eq("id", source_session_id).maybe_single().execute()

        source = source_result.data if source_result else None
        if not source:
            raise ValueError("Source session not found for paid quote")

        deep_session_id = self.session_db.create_session(
            user_id=str(source.get("user_id") or quote.get("user_id")),
            project_name=f"{source.get('project_name') or 'Project'} (Deep Match)",
            old_urls=source.get("old_urls") or [],
            new_urls=source.get("new_urls") or [],
            pipeline_type="content",
            source_session_id=source_session_id,
        )

        self.pricing_service.attach_deep_session(str(quote["id"]), deep_session_id)
        return str(deep_session_id)

    def _handle_project_checkout_completion(self, session: Dict[str, Any]) -> None:
        metadata = session.get("metadata", {}) or {}
        quote_id = metadata.get("quote_id")

        quote = None
        if quote_id:
            quote = self.pricing_service.get_quote_by_id(str(quote_id))
        if not quote:
            quote = self.pricing_service.get_quote_by_checkout_session(str(session.get("id")))

        if not quote:
            logger.error("Project checkout completed but quote not found. checkout_session=%s", session.get("id"))
            return

        if (quote.get("status") or "").lower() != "paid":
            self.pricing_service.mark_paid(
                quote_id=str(quote["id"]),
                stripe_payment_intent_id=session.get("payment_intent"),
            )
            quote = self.pricing_service.get_quote_by_id(str(quote["id"])) or quote

        if not quote.get("deep_session_id"):
            self._queue_deep_session_for_quote(quote)

    def _handle_agency_checkout_completion(self, session: Dict[str, Any]) -> None:
        metadata = session.get("metadata", {}) or {}
        customer_id = str(session.get("customer") or "")
        subscription_id = session.get("subscription")
        user_id = metadata.get("supabase_user_id") or self._resolve_user_id_from_customer(customer_id)

        if not user_id:
            logger.error(
                "Agency checkout completed but user could not be resolved. customer=%s session=%s",
                customer_id,
                session.get("id"),
            )
            return

        self.client.table("user_profiles").update(
            {
                "plan": "agency",
                "stripe_customer_id": customer_id or None,
                "stripe_subscription_id": subscription_id,
                "stripe_subscription_status": "active",
                "updated_at": _now_iso(),
            }
        ).eq("id", user_id).execute()

        if subscription_id:
            try:
                subscription = stripe.Subscription.retrieve(
                    subscription_id,
                    expand=["items.data.price"],
                )
                self._handle_subscription_updated(subscription)
            except Exception as e:
                logger.warning("Failed to sync subscription metadata for user %s: %s", user_id, e)

    def _handle_checkout_completed(self, session: Dict[str, Any]) -> None:
        metadata = session.get("metadata", {}) or {}
        checkout_type = str(metadata.get("checkout_type") or "").strip().lower()

        if checkout_type == "project" or metadata.get("quote_id"):
            self._handle_project_checkout_completion(session)
            return

        if checkout_type == "agency":
            self._handle_agency_checkout_completion(session)
            return

        logger.info("Checkout completed with unhandled type=%s session=%s", checkout_type, session.get("id"))

    def _handle_subscription_updated(self, subscription: Dict[str, Any]) -> None:
        customer_id = str(subscription.get("customer") or "")
        user_id = self._resolve_user_id_from_customer(customer_id)
        if not user_id:
            logger.error("No user found for Stripe customer %s", customer_id)
            return

        status = str(subscription.get("status") or "").lower()
        subscription_id = str(subscription.get("id") or "")
        overage_item_id = self._extract_overage_item_id(subscription)

        updates: Dict[str, Any] = {
            "stripe_subscription_id": subscription_id or None,
            "stripe_subscription_status": status or None,
            "stripe_overage_item_id": overage_item_id,
            "updated_at": _now_iso(),
        }

        if status in AGENCY_ACTIVE_STATUSES:
            updates["plan"] = "agency"
        elif status in AGENCY_CANCELLED_STATUSES:
            updates["plan"] = "free"
            updates["stripe_subscription_id"] = None
            updates["stripe_subscription_status"] = None
            updates["stripe_overage_item_id"] = None

        self.client.table("user_profiles").update(updates).eq("id", user_id).execute()

    def _handle_subscription_deleted(self, subscription: Dict[str, Any]) -> None:
        customer_id = str(subscription.get("customer") or "")
        user_id = self._resolve_user_id_from_customer(customer_id)
        if not user_id:
            return

        self.client.table("user_profiles").update(
            {
                "plan": "free",
                "stripe_subscription_id": None,
                "stripe_subscription_status": None,
                "stripe_overage_item_id": None,
                "updated_at": _now_iso(),
            }
        ).eq("id", user_id).execute()

    def handle_webhook_event(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        if not Config.STRIPE_WEBHOOK_SECRET:
            raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")

        try:
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                Config.STRIPE_WEBHOOK_SECRET,
            )
        except stripe.SignatureVerificationError:
            raise ValueError("Invalid webhook signature")

        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")

        if event_id and self._already_processed_webhook(event_id):
            return {"status": "ok", "event_type": event_type, "duplicate": True}

        data = event.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            self._handle_checkout_completed(data)
        elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
            self._handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            self._handle_subscription_deleted(data)
        else:
            logger.info("Unhandled Stripe webhook event: %s", event_type)

        self._record_webhook(event_id, event_type)
        return {"status": "ok", "event_type": event_type}

    # ---------------------------------------------------------------------
    # Agency metering
    # ---------------------------------------------------------------------

    def record_agency_usage(
        self,
        *,
        session_id: str,
        user_id: str,
        billable_pages: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Emit one metered usage event for an Agency deep-match session."""
        if billable_pages <= 0:
            return {"recorded": False, "reason": "invalid_billable_pages"}

        existing = self.client.table("agency_usage_events").select("id").eq(
            "session_id", str(session_id)
        ).maybe_single().execute()
        if existing and existing.data:
            return {"recorded": False, "duplicate": True, "event_id": existing.data.get("id")}

        profile = self.client.table("user_profiles").select(
            "plan,stripe_customer_id,stripe_subscription_id,stripe_overage_item_id"
        ).eq("id", user_id).maybe_single().execute()

        if not profile or not profile.data:
            return {"recorded": False, "reason": "profile_not_found"}

        if profile.data.get("plan") != "agency":
            return {"recorded": False, "reason": "not_agency_plan"}

        subscription_item_id = profile.data.get("stripe_overage_item_id")
        stripe_usage_record_id = None
        usage_metadata: Dict[str, Any] = metadata.copy() if isinstance(metadata, dict) else {}

        if subscription_item_id:
            try:
                usage_record = stripe.SubscriptionItem.create_usage_record(
                    subscription_item_id,
                    quantity=int(billable_pages),
                    timestamp=int(datetime.now(timezone.utc).timestamp()),
                    action="increment",
                )
                stripe_usage_record_id = usage_record.get("id")
                usage_metadata["stripe_metering"] = "sent"
            except Exception as e:
                usage_metadata["stripe_metering"] = "failed"
                usage_metadata["stripe_error"] = str(e)[:500]
                logger.error(
                    "Failed to submit Stripe metered usage for session %s: %s",
                    session_id,
                    e,
                )
        else:
            usage_metadata["stripe_metering"] = "not_configured"

        event_row = self.pricing_service.record_agency_usage_event(
            session_id=session_id,
            user_id=user_id,
            billable_pages=int(billable_pages),
            stripe_customer_id=profile.data.get("stripe_customer_id"),
            stripe_subscription_id=profile.data.get("stripe_subscription_id"),
            stripe_subscription_item_id=subscription_item_id,
            stripe_usage_record_id=stripe_usage_record_id,
            metadata=usage_metadata,
        )

        return {
            "recorded": bool(event_row),
            "event_id": event_row.get("id") if event_row else None,
            "stripe_usage_record_id": stripe_usage_record_id,
        }

    # ---------------------------------------------------------------------
    # Billing status
    # ---------------------------------------------------------------------

    def get_billing_status(self, user_id: str) -> Dict[str, Any]:
        result = self.client.table("user_profiles").select(
            "plan,stripe_customer_id,stripe_subscription_id,stripe_subscription_status,stripe_overage_item_id"
        ).eq("id", user_id).maybe_single().execute()

        if not result or not result.data:
            return {
                "plan": "free",
                "manage_portal_available": False,
                "agency": {
                    "has_subscription": False,
                    "subscription_id": None,
                    "status": None,
                    "current_period_start": None,
                    "current_period_end": None,
                    "cancel_at_period_end": False,
                    "usage_pages": 0,
                    "overage_enabled": bool(Config.STRIPE_PRICE_ID_AGENCY_OVERAGE),
                },
            }

        profile = result.data
        plan = str(profile.get("plan") or "free")
        subscription_id = profile.get("stripe_subscription_id")
        subscription_status = profile.get("stripe_subscription_status")
        overage_item_id = profile.get("stripe_overage_item_id")

        period_start_iso = None
        period_end_iso = None
        cancel_at_period_end = False

        if subscription_id:
            try:
                sub = stripe.Subscription.retrieve(
                    subscription_id,
                    expand=["items.data.price"],
                )
                subscription_status = sub.get("status") or subscription_status
                period_start_iso = _unix_to_iso(sub.get("current_period_start"))
                period_end_iso = _unix_to_iso(sub.get("current_period_end"))
                cancel_at_period_end = bool(sub.get("cancel_at_period_end"))

                detected_overage_item = self._extract_overage_item_id(sub)
                if detected_overage_item != overage_item_id:
                    overage_item_id = detected_overage_item
                    self.client.table("user_profiles").update(
                        {
                            "stripe_overage_item_id": overage_item_id,
                            "stripe_subscription_status": subscription_status,
                            "updated_at": _now_iso(),
                        }
                    ).eq("id", user_id).execute()
            except Exception as e:
                logger.warning("Failed to retrieve Stripe subscription for status view: %s", e)

        usage_pages = 0
        if plan == "agency":
            if period_start_iso and period_end_iso:
                start_dt = datetime.fromisoformat(period_start_iso)
                end_dt = datetime.fromisoformat(period_end_iso)
            else:
                end_dt = datetime.now(timezone.utc)
                start_dt = end_dt - timedelta(days=30)

            usage_pages = self.pricing_service.get_agency_usage_pages(
                user_id=user_id,
                start_ts=start_dt,
                end_ts=end_dt,
            )

        return {
            "plan": plan,
            "pricing_version": PRICING_VERSION,
            "manage_portal_available": bool(profile.get("stripe_customer_id")),
            "agency": {
                "has_subscription": bool(subscription_id) and plan == "agency",
                "subscription_id": subscription_id,
                "status": subscription_status,
                "current_period_start": period_start_iso,
                "current_period_end": period_end_iso,
                "cancel_at_period_end": cancel_at_period_end,
                "usage_pages": usage_pages,
                "overage_enabled": bool(overage_item_id and Config.STRIPE_PRICE_ID_AGENCY_OVERAGE),
            },
        }
