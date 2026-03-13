# Stripe Go-Live Checklist

Use this checklist to transition RedirX billing from test mode to production.

---

## Phase 1: Fix Test-Mode Checkout (Do Now)

These unblock Agency checkout in test mode for end-to-end testing.

- [ ] **Add price IDs to local `.env`**:
  ```bash
  # Append to .env (test-mode IDs from current Stripe account)
  echo 'STRIPE_PRICE_ID_AGENCY_MONTHLY=price_1T7qI7DZLn8nNLxEcF4XNmfD' >> .env
  echo 'STRIPE_PRICE_ID_AGENCY_ANNUAL=price_1T7qIdDZLn8nNLxENU09s1Fa' >> .env
  echo 'STRIPE_PRICE_ID_AGENCY_OVERAGE=price_1T7qLgDZLn8nNLxEgXjWVTFD' >> .env
  ```
- [ ] **Find your meter event name** and set it:
  ```bash
  # Check Stripe Dashboard → Billing → Meters for the event name
  echo 'STRIPE_METER_EVENT_NAME=your_meter_event_name' >> .env
  ```
- [ ] **Run preflight**:
  ```bash
  python scripts/stripe_preflight.py
  ```
- [ ] **Test Agency checkout end-to-end** locally with `python dev.py`:
  - Click "Subscribe" for Agency monthly
  - Use Stripe test card `4242 4242 4242 4242`
  - Verify webhook fires and user plan updates to "agency"
  - Verify billing portal opens
- [ ] **Test project (one-time) checkout** with a real quote

---

## Phase 2: Prepare Live-Mode Stripe

- [ ] **Activate your Stripe account** (complete Stripe onboarding if not done)
- [ ] **Create live-mode products and prices** in Stripe Dashboard:
  - Product: "RedirX Agency"
    - Price: $349/mo recurring (monthly, licensed)
    - Price: $3,499/yr recurring (annual, licensed)
    - Price: metered recurring (monthly, for overages)
  - Create a billing Meter with the same event name
- [ ] **Record the live price IDs** — you'll need them for Render env vars
- [ ] **Create live webhook endpoint**:
  - URL: `https://redirx-api.onrender.com/api/billing/webhook`
  - Events to subscribe:
    - `checkout.session.completed`
    - `customer.subscription.created`
    - `customer.subscription.updated`
    - `customer.subscription.deleted`
  - Copy the signing secret (`whsec_*`)

---

## Phase 3: Deploy to Render

- [ ] **Render services confirmed** (shared env group `evm-d59il0ggjchc73aorm50`):
  - Backend: `srv-d59il0shg0os73cctuug` (redirx-api)
  - Worker: `srv-d59iq2v5r7bs7399t4c0` (redirx-worker)
  - Frontend: `srv-d59isemuk2gs73e7pvg0` (redirx-frontend, `app.redirx.dev`)
- [ ] **Fix missing webhook event**: In Stripe Dashboard → Webhooks → `redirx-api.onrender.com` endpoint → add `customer.subscription.created`
- [ ] **Set env vars on Render backend**:
  | Variable | Value |
  |----------|-------|
  | `STRIPE_SECRET_KEY` | `sk_live_...` |
  | `STRIPE_WEBHOOK_SECRET` | `whsec_...` (from live endpoint) |
  | `STRIPE_PRICE_ID_AGENCY_MONTHLY` | `price_...` (live) |
  | `STRIPE_PRICE_ID_AGENCY_ANNUAL` | `price_...` (live) |
  | `STRIPE_PRICE_ID_AGENCY_OVERAGE` | `price_...` (live) |
  | `STRIPE_METER_EVENT_NAME` | (same event name) |
- [ ] **Set env vars on Render worker** (same as backend, minus `STRIPE_WEBHOOK_SECRET`)
- [ ] **Redeploy backend and worker** after setting env vars
- [ ] **Run preflight against production** (set env vars locally to live values temporarily):
  ```bash
  STRIPE_SECRET_KEY=sk_live_... python scripts/stripe_preflight.py
  ```

---

## Phase 4: Verify Production

- [ ] **Send a test webhook** from Stripe Dashboard → Webhooks → Send test webhook
- [ ] **Check Stripe Dashboard → Webhooks** for successful deliveries
- [ ] **Create a real Agency subscription** with a test/personal account
- [ ] **Verify in Supabase** that `user_profiles.plan` updated to `agency`
- [ ] **Trigger a Deep Match** to verify metered usage event reaches Stripe
- [ ] **Open billing portal** and verify subscription management works

---

## Phase 5: Cleanup

- [ ] **Archive legacy products** in Stripe Dashboard (Starter, Growth, Scale, Founder, Deep Match Credits)
- [ ] **Cancel test subscriptions** from Phase 1 testing
- [ ] **Review customer `cus_TzgJ...`** — has both legacy Scale + new Agency sub
- [ ] **Update `.env.example`** to include `STRIPE_METER_EVENT_NAME`
- [ ] **Remove test card data** from any logs or screenshots

---

## Quick Reference: Required Webhook Events

```
checkout.session.completed
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
```

## Quick Reference: Test Cards

| Card | Behavior |
|------|----------|
| `4242 4242 4242 4242` | Succeeds |
| `4000 0000 0000 3220` | 3D Secure required |
| `4000 0000 0000 9995` | Declined |
