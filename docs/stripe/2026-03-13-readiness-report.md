# Stripe Production Readiness Report — 2026-03-13

## Account State

| Field | Value |
|-------|-------|
| Account ID | `acct_1T1fx6DZLn8nNLxE` |
| Display name | RedirX sandbox |
| Mode | **TEST** (`sk_test_*`) |
| Webhook secret | Set locally (`whsec_*`) |

> The account is currently operating in **test mode**. No real payments can be collected.

---

## Products & Prices in Stripe

### Active Product: "RedirX Agency" (`prod_U62McCzqAS9eTq`)

| Price ID | Type | Amount | Interval | Usage |
|----------|------|--------|----------|-------|
| `price_1T7qI7DZLn8nNLxEcF4XNmfD` | Recurring | $349.00 | Monthly | Licensed |
| `price_1T7qIdDZLn8nNLxENU09s1Fa` | Recurring | $3,499.00 | Annual | Licensed |
| `price_1T7qLgDZLn8nNLxEgXjWVTFD` | Recurring | Metered | Monthly | Metered (meter: `mtr_test_*`) |

### Legacy Products (pre-v2, still in Stripe)

| Product | Price | Type |
|---------|-------|------|
| Starter Monthly | $59/mo | Recurring |
| Growth Monthly | $179/mo | Recurring |
| Scale Monthly | $449/mo | Recurring |
| Starter Annual | $590/yr | Recurring |
| Growth Annual | $1,790/yr | Recurring |
| Scale Annual | $4,490/yr | Recurring |
| Deep Match Credits | $2.00 | One-time |
| Founder | $999.00 | One-time |

**Recommendation**: Archive legacy products in Stripe Dashboard to prevent confusion. Existing subscriptions on legacy prices will continue working.

---

## Environment Variable Audit

### Local `.env`

| Variable | Status | Notes |
|----------|--------|-------|
| `STRIPE_SECRET_KEY` | Set | `sk_test_*` (test mode) |
| `STRIPE_WEBHOOK_SECRET` | Set | `whsec_*` |
| `STRIPE_PRICE_ID_AGENCY_MONTHLY` | **MISSING** | Not in `.env` at all |
| `STRIPE_PRICE_ID_AGENCY_ANNUAL` | **MISSING** | Not in `.env` at all |
| `STRIPE_PRICE_ID_AGENCY_OVERAGE` | **MISSING** | Not in `.env` at all |
| `STRIPE_METER_EVENT_NAME` | **MISSING** | Not in `.env` at all |

### Impact of Missing Vars

1. **Agency checkout is completely broken** — `create_agency_checkout_session()` raises `ValueError("Agency monthly price is not configured")` immediately.
2. **Metered billing silently skipped** — `record_agency_usage()` falls through to `"not_configured"` branch; no usage events reach Stripe.
3. **Project (one-time) checkout works** — uses inline `price_data`, not price IDs.

### Render Production (verified 2026-03-13)

All 3 Render services share environment group `evm-d59il0ggjchc73aorm50`:

| Service | ID | Type | URL |
|---------|-----|------|-----|
| redirx-api | `srv-d59il0shg0os73cctuug` | Web service | `https://redirx-api.onrender.com` |
| redirx-worker | `srv-d59iq2v5r7bs7399t4c0` | Background worker | — |
| redirx-frontend | `srv-d59isemuk2gs73e7pvg0` | Static site | `https://app.redirx.dev` (custom domain) |

**Evidence from production logs (Mar 11)**:
- Stripe webhooks returning HTTP 200 → `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` ARE set
- No checkout requests attempted → cannot confirm `STRIPE_PRICE_ID_*` vars from logs
- Render MCP does not expose a "list env vars" API, so exact values cannot be read programmatically

**Webhook endpoint in Stripe Dashboard**:
- URL: `https://redirx-api.onrender.com/api/billing/webhook`
- Status: Active (200 responses confirmed)
- **Missing event**: `customer.subscription.created` is NOT subscribed — new Agency subscriptions may fail to sync if the `checkout.session.completed` fallback path errors

**Action required**: Verify price ID vars are set on Render. If using shared env group, setting them once applies to all 3 services. Add the missing webhook event.

---

## Subscriptions

| Subscription | Customer | Status | Plan |
|-------------|----------|--------|------|
| `sub_1T7qRK...` | `cus_TzgJ...` | Active | Agency (monthly + overage) |
| `sub_1T2Ol3...` | `cus_U0PQ...` | Active | Starter (legacy) |
| `sub_1T2Oa2...` | `cus_U0PO...` | Active | Starter (legacy) |
| `sub_1T2GlK...` | `cus_U0HJ...` | Active | Growth (legacy) |
| `sub_1T1h2K...` | `cus_TzgJ...` | Active | Scale (legacy) |

**Note**: Customer `cus_TzgJ...` has BOTH a legacy Scale subscription AND a new Agency subscription. This may cause plan detection issues.

---

## Code Quality Assessment

### Strengths
- `StripeService.__init__()` fails fast if `STRIPE_SECRET_KEY` missing
- `create_agency_checkout_session()` validates all 3 price IDs before proceeding
- Webhook idempotency via `stripe_webhook_events` table (tested)
- Webhook signature verification enforced
- `dev.py` auto-starts `stripe listen` for local webhook forwarding
- Good test coverage for checkout and webhook flows

### Gaps
1. **No startup validation** — missing price IDs only surface when a user clicks checkout, not at boot
2. **No mode guard** — nothing prevents mixing test keys with live price IDs (or vice versa)
3. **No Stripe health check endpoint** — ops has no way to verify billing is functional without triggering a real checkout
4. **Legacy subscription overlap** — no logic to cancel/migrate legacy subs when Agency is activated
5. **`STRIPE_METER_EVENT_NAME`** not documented in `.env.example`

---

## Blocker List (Priority Order)

### P0 — Must fix before any checkout works

1. **Set `STRIPE_PRICE_ID_AGENCY_MONTHLY`** in `.env` → `price_1T7qI7DZLn8nNLxEcF4XNmfD`
2. **Set `STRIPE_PRICE_ID_AGENCY_ANNUAL`** in `.env` → `price_1T7qIdDZLn8nNLxENU09s1Fa`
3. **Set `STRIPE_PRICE_ID_AGENCY_OVERAGE`** in `.env` → `price_1T7qLgDZLn8nNLxEgXjWVTFD`
4. **Set the same 3 vars on Render** (backend + worker services)

### P1 — Must fix before going live

5. **Switch to live-mode Stripe keys** (`sk_live_*`, `whsec_*` from live endpoint)
6. **Create live-mode products/prices** in Stripe (test-mode objects don't exist in live mode)
7. **Create live webhook endpoint** in Stripe Dashboard → `https://your-backend.onrender.com/api/billing/webhook`
8. **Subscribe webhook to events**: `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`
9. **Set `STRIPE_WEBHOOK_SECRET`** on Render backend to the live endpoint's signing secret

### P2 — Should fix soon

10. **Set `STRIPE_METER_EVENT_NAME`** so agency overage billing actually works
11. **Add `STRIPE_METER_EVENT_NAME` to `.env.example`** for documentation
12. **Archive legacy products** in Stripe Dashboard
13. **Cancel duplicate legacy subscriptions** for customers who migrated to Agency
14. **Add startup validation** for Stripe config (see preflight script)
