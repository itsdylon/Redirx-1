#!/usr/bin/env python3
"""
Stripe preflight checker for RedirX.

Validates environment variables, Stripe API connectivity, product/price existence,
mode consistency (test vs live), and webhook configuration.

Usage:
    python scripts/stripe_preflight.py          # Check local .env
    python scripts/stripe_preflight.py --strict  # Fail on warnings too
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _redact(value: str | None) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 12:
        return value[:4] + "***"
    return value[:8] + "***" + value[-4:]


class PreflightResult:
    def __init__(self):
        self.passes: list[str] = []
        self.warnings: list[str] = []
        self.failures: list[str] = []

    def ok(self, msg: str):
        self.passes.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def fail(self, msg: str):
        self.failures.append(msg)

    def print_report(self):
        print("\n" + "=" * 60)
        print("  RedirX Stripe Preflight Report")
        print("=" * 60 + "\n")

        for msg in self.passes:
            print(f"  [PASS] {msg}")
        for msg in self.warnings:
            print(f"  [WARN] {msg}")
        for msg in self.failures:
            print(f"  [FAIL] {msg}")

        print()
        total = len(self.passes) + len(self.warnings) + len(self.failures)
        print(f"  {len(self.passes)}/{total} passed, {len(self.warnings)} warnings, {len(self.failures)} failures")
        print("=" * 60 + "\n")

    @property
    def is_ok(self) -> bool:
        return len(self.failures) == 0

    @property
    def is_strict_ok(self) -> bool:
        return len(self.failures) == 0 and len(self.warnings) == 0


def check_env_vars(result: PreflightResult) -> dict[str, str | None]:
    """Check that all required Stripe env vars are set."""
    required = {
        "STRIPE_SECRET_KEY": os.getenv("STRIPE_SECRET_KEY"),
        "STRIPE_WEBHOOK_SECRET": os.getenv("STRIPE_WEBHOOK_SECRET"),
        "STRIPE_PRICE_ID_AGENCY_MONTHLY": os.getenv("STRIPE_PRICE_ID_AGENCY_MONTHLY"),
        "STRIPE_PRICE_ID_AGENCY_ANNUAL": os.getenv("STRIPE_PRICE_ID_AGENCY_ANNUAL"),
        "STRIPE_PRICE_ID_AGENCY_OVERAGE": os.getenv("STRIPE_PRICE_ID_AGENCY_OVERAGE"),
    }

    optional = {
        "STRIPE_METER_EVENT_NAME": os.getenv("STRIPE_METER_EVENT_NAME"),
    }

    for name, value in required.items():
        if not value or value.startswith("price_xxx") or value == "sk_test_your-stripe-secret-key":
            result.fail(f"{name} is not set (value: {_redact(value)})")
        else:
            result.ok(f"{name} is set ({_redact(value)})")

    for name, value in optional.items():
        if not value:
            result.warn(f"{name} is not set — agency metered billing will be skipped")
        else:
            result.ok(f"{name} is set ({_redact(value)})")

    return {**required, **optional}


def check_mode_consistency(result: PreflightResult, env: dict[str, str | None]):
    """Verify all Stripe artifacts are in the same mode (test or live)."""
    secret_key = env.get("STRIPE_SECRET_KEY") or ""

    if secret_key.startswith("sk_test_"):
        mode = "test"
    elif secret_key.startswith("sk_live_"):
        mode = "live"
    else:
        result.fail(f"STRIPE_SECRET_KEY has unexpected prefix: {_redact(secret_key)}")
        return

    result.ok(f"Stripe mode: {mode}")

    if mode == "test":
        result.warn("Running in TEST mode — real payments will not be collected")

    # Check price IDs for mode consistency (test prices have no prefix, but
    # the key mode determines which Stripe environment they resolve in)
    webhook_secret = env.get("STRIPE_WEBHOOK_SECRET") or ""
    if mode == "live" and "test" in webhook_secret.lower():
        result.fail("STRIPE_WEBHOOK_SECRET looks like a test secret but key is live mode")
    elif mode == "test" and webhook_secret and "test" not in webhook_secret.lower():
        result.warn("STRIPE_WEBHOOK_SECRET may be a live secret but key is test mode")


def check_stripe_api(result: PreflightResult, env: dict[str, str | None]):
    """Validate Stripe API connectivity and verify price objects exist."""
    secret_key = env.get("STRIPE_SECRET_KEY")
    if not secret_key or secret_key.startswith("sk_test_your"):
        result.fail("Skipping API checks — STRIPE_SECRET_KEY is placeholder")
        return

    try:
        import stripe
    except ImportError:
        result.fail("stripe Python package is not installed")
        return

    stripe.api_key = secret_key

    # 1. Account connectivity
    try:
        account = stripe.Account.retrieve()
        name = account.get("settings", {}).get("dashboard", {}).get("display_name") or account.get("id")
        result.ok(f"Stripe API connected — account: {name}")
    except stripe.AuthenticationError:
        result.fail("Stripe API authentication failed — check STRIPE_SECRET_KEY")
        return
    except Exception as e:
        result.fail(f"Stripe API error: {e}")
        return

    # 2. Verify each price ID exists and is active
    price_vars = {
        "STRIPE_PRICE_ID_AGENCY_MONTHLY": env.get("STRIPE_PRICE_ID_AGENCY_MONTHLY"),
        "STRIPE_PRICE_ID_AGENCY_ANNUAL": env.get("STRIPE_PRICE_ID_AGENCY_ANNUAL"),
        "STRIPE_PRICE_ID_AGENCY_OVERAGE": env.get("STRIPE_PRICE_ID_AGENCY_OVERAGE"),
    }

    for var_name, price_id in price_vars.items():
        if not price_id or price_id.startswith("price_xxx"):
            continue  # Already flagged as missing

        try:
            price = stripe.Price.retrieve(price_id, expand=["product"])
            product_name = ""
            if hasattr(price, "product") and isinstance(price.product, stripe.Product):
                product_name = f" (product: {price.product.name})"
                if not price.product.active:
                    result.warn(f"{var_name}: product is INACTIVE{product_name}")

            if not price.active:
                result.fail(f"{var_name}: price {price_id} is INACTIVE{product_name}")
            else:
                recurring_info = ""
                if price.recurring:
                    usage = price.recurring.get("usage_type", "licensed")
                    interval = price.recurring.get("interval", "?")
                    recurring_info = f" [{usage}/{interval}]"
                amount_info = f" ${price.unit_amount / 100:.2f}" if price.unit_amount else " (metered)"
                result.ok(f"{var_name}: active{amount_info}{recurring_info}{product_name}")
        except stripe.InvalidRequestError:
            result.fail(f"{var_name}: price {_redact(price_id)} does NOT exist in Stripe")
        except Exception as e:
            result.fail(f"{var_name}: error retrieving price — {e}")

    # 3. Check meter event name
    meter_name = env.get("STRIPE_METER_EVENT_NAME")
    if meter_name:
        try:
            meters = stripe.billing.Meter.list(limit=10)
            found = False
            for meter in meters.auto_paging_iter():
                if meter.event_name == meter_name:
                    if meter.status == "active":
                        result.ok(f"STRIPE_METER_EVENT_NAME: meter '{meter_name}' exists and is active")
                    else:
                        result.fail(f"STRIPE_METER_EVENT_NAME: meter '{meter_name}' exists but is {meter.status}")
                    found = True
                    break
            if not found:
                result.fail(f"STRIPE_METER_EVENT_NAME: no meter with event_name='{meter_name}' found")
        except Exception as e:
            result.warn(f"STRIPE_METER_EVENT_NAME: could not verify meter — {e}")

    # 4. Check for active webhook endpoints (live mode only matters)
    try:
        endpoints = stripe.WebhookEndpoint.list(limit=10)
        if endpoints.data:
            for ep in endpoints.data:
                status = ep.get("status", "unknown")
                url = ep.get("url", "unknown")
                events = ep.get("enabled_events", [])
                required_events = {
                    "checkout.session.completed",
                    "customer.subscription.created",
                    "customer.subscription.updated",
                    "customer.subscription.deleted",
                }
                missing_events = required_events - set(events)
                has_all = "*" in events or not missing_events

                if status == "enabled" and has_all:
                    result.ok(f"Webhook endpoint: {url} (enabled, all events)")
                elif status == "enabled":
                    result.warn(f"Webhook endpoint: {url} — missing events: {missing_events}")
                else:
                    result.warn(f"Webhook endpoint: {url} — status: {status}")
        else:
            result.warn("No webhook endpoints configured in Stripe Dashboard")
    except Exception as e:
        result.warn(f"Could not list webhook endpoints: {e}")


def main():
    strict = "--strict" in sys.argv

    result = PreflightResult()

    print("\nChecking environment variables...")
    env = check_env_vars(result)

    print("Checking mode consistency...")
    check_mode_consistency(result, env)

    print("Checking Stripe API connectivity and objects...")
    check_stripe_api(result, env)

    result.print_report()

    if strict and not result.is_strict_ok:
        print("STRICT MODE: exiting with code 1 due to warnings/failures\n")
        sys.exit(1)
    elif not result.is_ok:
        print("Exiting with code 1 due to failures\n")
        sys.exit(1)
    else:
        print("All critical checks passed.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
