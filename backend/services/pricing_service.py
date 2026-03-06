"""
Pricing + quote lifecycle for RedirX Pricing V2.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redirx.database import SupabaseClient


PRICING_VERSION = "v1_2026_03"
USD = "usd"
CONTACT_REQUIRED_PAGE_THRESHOLD = 100_000


@dataclass(frozen=True)
class PricingBand:
    start: int
    end: int | None
    unit_price: Decimal


PRICING_BANDS: tuple[PricingBand, ...] = (
    PricingBand(start=1, end=500, unit_price=Decimal("0.10")),
    PricingBand(start=501, end=2_000, unit_price=Decimal("0.05")),
    PricingBand(start=2_001, end=5_000, unit_price=Decimal("0.035")),
    PricingBand(start=5_001, end=15_000, unit_price=Decimal("0.02")),
    PricingBand(start=15_001, end=50_000, unit_price=Decimal("0.009")),
    PricingBand(start=50_001, end=100_000, unit_price=Decimal("0.007")),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_cents(amount: Decimal) -> int:
    cents = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def _to_decimal_string(amount: Decimal, places: int = 3) -> str:
    q = Decimal("1") if places == 0 else Decimal(f"1.{'0' * places}")
    return format(amount.quantize(q, rounding=ROUND_HALF_UP), "f")


def calculate_graduated_price(page_count: int) -> dict[str, Any]:
    if page_count <= 0:
        raise ValueError("page_count must be greater than 0")

    if page_count > CONTACT_REQUIRED_PAGE_THRESHOLD:
        return {
            "pricing_version": PRICING_VERSION,
            "currency": USD,
            "contact_required": True,
            "billable_pages": page_count,
            "line_items": [],
            "subtotal_usd": None,
            "subtotal_cents": None,
            "effective_rate_usd": None,
        }

    remaining = page_count
    line_items: list[dict[str, Any]] = []
    subtotal = Decimal("0")

    for band in PRICING_BANDS:
        if remaining <= 0:
            break

        band_size = (band.end - band.start + 1) if band.end is not None else remaining
        billable_in_band = min(remaining, band_size)
        if billable_in_band <= 0:
            continue

        amount = Decimal(billable_in_band) * band.unit_price
        subtotal += amount

        band_end_page = (band.start + billable_in_band - 1)
        line_items.append(
            {
                "from_page": band.start,
                "to_page": band_end_page,
                "pages": billable_in_band,
                "unit_price_usd": _to_decimal_string(band.unit_price, places=3),
                "amount_usd": _to_decimal_string(amount, places=2),
                "amount_cents": _to_cents(amount),
            }
        )

        remaining -= billable_in_band

    effective = subtotal / Decimal(page_count)
    return {
        "pricing_version": PRICING_VERSION,
        "currency": USD,
        "contact_required": False,
        "billable_pages": page_count,
        "line_items": line_items,
        "subtotal_usd": _to_decimal_string(subtotal, places=2),
        "subtotal_cents": _to_cents(subtotal),
        "effective_rate_usd": _to_decimal_string(effective, places=3),
    }


class PricingService:
    """Quote persistence and project unlock status helpers."""

    def __init__(self):
        # Use a fresh service-role client to avoid auth state contamination.
        self.client = SupabaseClient.get_admin_client()

    @staticmethod
    def compute_billable_pages(old_url_count: int, new_url_count: int) -> int:
        return max(old_url_count, new_url_count)

    def estimate(self, page_count: int) -> dict[str, Any]:
        return calculate_graduated_price(page_count)

    def create_or_refresh_quote(self, source_session_id: UUID | str, user_id: str) -> dict[str, Any]:
        source_session_id = str(source_session_id)

        session_result = self.client.table("migration_sessions").select(
            "id,user_id,old_urls,new_urls,status,is_preview,pipeline_type"
        ).eq("id", source_session_id).maybe_single().execute()

        session = session_result.data if session_result else None
        if not session:
            raise ValueError("Source session not found")
        if session.get("user_id") != user_id:
            raise ValueError("Source session does not belong to this user")
        if session.get("is_preview"):
            raise ValueError("Preview sessions cannot be quoted")
        if session.get("pipeline_type") != "url_only":
            raise ValueError("Only Quick Match source sessions can be quoted")
        if (session.get("status") or "").lower() not in {"completed", "permanently_failed"}:
            raise ValueError("Source session must be completed before quoting")

        old_urls = session.get("old_urls") or []
        new_urls = session.get("new_urls") or []
        old_count = len(old_urls)
        new_count = len(new_urls)
        billable_pages = self.compute_billable_pages(old_count, new_count)
        if billable_pages <= 0:
            raise ValueError("Source session has no billable pages")
        estimate = self.estimate(billable_pages)

        status = "contact_required" if estimate["contact_required"] else "draft"

        payload = {
            "source_session_id": source_session_id,
            "user_id": user_id,
            "old_url_count": old_count,
            "new_url_count": new_count,
            "billable_pages": billable_pages,
            "pricing_version": estimate["pricing_version"],
            "currency": estimate["currency"],
            "line_items": estimate["line_items"],
            "subtotal_cents": estimate["subtotal_cents"],
            "status": status,
            "updated_at": _now_iso(),
        }

        existing = self.client.table("project_pricing_quotes").select(
            "id,status,deep_session_id"
        ).eq("source_session_id", source_session_id).maybe_single().execute()

        existing_data = existing.data if existing else None
        if existing_data:
            # Preserve paid state / linked deep session if quote already completed.
            if existing_data.get("status") == "paid":
                payload["status"] = "paid"
            if existing_data.get("deep_session_id"):
                payload["deep_session_id"] = existing_data["deep_session_id"]

            result = self.client.table("project_pricing_quotes").update(payload).eq(
                "source_session_id", source_session_id
            ).execute()
            return result.data[0] if (result and result.data) else {**payload, "id": existing_data["id"]}

        payload["created_at"] = _now_iso()
        result = self.client.table("project_pricing_quotes").insert(payload).execute()
        return result.data[0] if (result and result.data) else payload

    def get_quote_for_source(self, source_session_id: UUID | str, user_id: str) -> dict[str, Any] | None:
        result = self.client.table("project_pricing_quotes").select("*").eq(
            "source_session_id", str(source_session_id)
        ).eq("user_id", user_id).maybe_single().execute()
        return result.data if result else None

    def get_quote_by_id(self, quote_id: str) -> dict[str, Any] | None:
        result = self.client.table("project_pricing_quotes").select("*").eq(
            "id", quote_id
        ).maybe_single().execute()
        return result.data if result else None

    def get_quote_by_checkout_session(self, stripe_checkout_session_id: str) -> dict[str, Any] | None:
        result = self.client.table("project_pricing_quotes").select("*").eq(
            "stripe_checkout_session_id", stripe_checkout_session_id
        ).maybe_single().execute()
        return result.data if result else None

    def mark_checkout_created(self, quote_id: str, stripe_checkout_session_id: str) -> dict[str, Any] | None:
        result = self.client.table("project_pricing_quotes").update(
            {
                "status": "checkout_created",
                "stripe_checkout_session_id": stripe_checkout_session_id,
                "checkout_created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        ).eq("id", quote_id).execute()
        return result.data[0] if (result and result.data) else None

    def mark_paid(self, quote_id: str, stripe_payment_intent_id: str | None = None) -> dict[str, Any] | None:
        updates: dict[str, Any] = {
            "status": "paid",
            "paid_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if stripe_payment_intent_id:
            updates["stripe_payment_intent_id"] = stripe_payment_intent_id

        result = self.client.table("project_pricing_quotes").update(updates).eq(
            "id", quote_id
        ).execute()
        return result.data[0] if (result and result.data) else None

    def attach_deep_session(self, quote_id: str, deep_session_id: UUID | str) -> dict[str, Any] | None:
        result = self.client.table("project_pricing_quotes").update(
            {
                "deep_session_id": str(deep_session_id),
                "updated_at": _now_iso(),
            }
        ).eq("id", quote_id).execute()
        return result.data[0] if (result and result.data) else None

    def get_unlock_status(self, source_session_id: UUID | str, user_id: str) -> dict[str, Any]:
        source_session_id = str(source_session_id)

        session = self.client.table("migration_sessions").select(
            "id,user_id,status"
        ).eq("id", source_session_id).maybe_single().execute()

        if not session or not session.data:
            raise ValueError("Source session not found")
        if session.data.get("user_id") != user_id:
            raise ValueError("Source session does not belong to this user")

        quote = self.get_quote_for_source(source_session_id, user_id)
        if not quote:
            return {
                "source_session_id": source_session_id,
                "has_quote": False,
                "quote_status": None,
                "contact_required": False,
                "billable_pages": None,
                "subtotal_cents": None,
                "is_unlocked": False,
                "deep_session_id": None,
                "deep_session_status": None,
            }

        deep_session_id = quote.get("deep_session_id")
        deep_status = None
        if deep_session_id:
            deep_session = self.client.table("migration_sessions").select(
                "status"
            ).eq("id", str(deep_session_id)).maybe_single().execute()
            if deep_session and deep_session.data:
                deep_status = deep_session.data.get("status")

        return {
            "source_session_id": source_session_id,
            "has_quote": True,
            "quote_id": quote.get("id"),
            "quote_status": quote.get("status"),
            "contact_required": quote.get("status") == "contact_required",
            "billable_pages": quote.get("billable_pages"),
            "subtotal_cents": quote.get("subtotal_cents"),
            "currency": quote.get("currency", USD),
            "pricing_version": quote.get("pricing_version"),
            "is_unlocked": quote.get("status") == "paid",
            "deep_session_id": str(deep_session_id) if deep_session_id else None,
            "deep_session_status": deep_status,
        }

    def get_agency_usage_pages(self, user_id: str, start_ts: datetime, end_ts: datetime) -> int:
        result = self.client.table("agency_usage_events").select("billable_pages").eq(
            "user_id", user_id
        ).gte(
            "event_timestamp", start_ts.isoformat()
        ).lt(
            "event_timestamp", end_ts.isoformat()
        ).execute()

        return sum(int(row.get("billable_pages") or 0) for row in (result.data or []))

    def record_agency_usage_event(
        self,
        *,
        session_id: UUID | str,
        user_id: str,
        billable_pages: int,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        stripe_subscription_item_id: str | None = None,
        stripe_usage_record_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = {
            "session_id": str(session_id),
            "user_id": user_id,
            "billable_pages": int(billable_pages),
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_subscription_item_id": stripe_subscription_item_id,
            "stripe_usage_record_id": stripe_usage_record_id,
            "metadata": metadata or {},
        }

        existing = self.client.table("agency_usage_events").select("*").eq(
            "session_id", str(session_id)
        ).maybe_single().execute()
        if existing and existing.data:
            return existing.data

        result = self.client.table("agency_usage_events").insert(payload).execute()
        return result.data[0] if (result and result.data) else None
