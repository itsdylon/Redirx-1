# Pricing V2 Subagent Orchestration

## Goal
Ship the pricing-system cutover quickly with high confidence and low regression risk.

## Parallel Workstreams

### 1. Schema/Data Subagent
Owner: Database engineer

Deliverables:
- Apply/verify `021_pricing_v2_core.sql` and `022_pricing_v2_cleanup.sql`
- Confirm constraints/indexes on `project_pricing_quotes` and `agency_usage_events`
- Validate data reset (`plan=free`, legacy subscription fields cleared)
- Produce rollback snapshot identifier and restore command checklist

### 2. Billing/Stripe Backend Subagent
Owner: API engineer

Deliverables:
- Endpoints:
  - `GET /api/pricing/estimate`
  - `POST /api/pricing/quote`
  - `POST /api/billing/project/checkout`
  - `POST /api/billing/agency/checkout`
  - `GET /api/billing/status`
- Webhook handling for checkout + subscription lifecycle
- Quote unlock idempotency + deep-run queueing
- Legacy endpoint deprecation responses (`410`)

### 3. Pipeline/Worker Subagent
Owner: Worker engineer

Deliverables:
- Remove credit/quota gates from upload flow
- Enforce free-upload behavior (`url_only` only for free)
- Add unlock-status API (`GET /api/projects/:source_session_id/unlock-status`)
- Worker metering dispatch for agency content runs
- Ensure metering idempotency (`UNIQUE(session_id)`)

### 4. Frontend Subagent
Owner: Frontend engineer

Deliverables:
- Add `/pricing` page with:
  - Graduated slider estimate panel
  - Source-session quote panel
  - Agency monthly/annual checkout card
- Update review flow with unlock-status states
- Update upload flow terminology and CTA links
- Replace settings billing tab with free/agency model
- Remove trial/founder/admin trial routes/components/api clients

### 5. QA Subagent
Owner: QA engineer

Deliverables:
- Backend unit/API tests for pricing calculator and billing routes
- Frontend vitest updates for Settings/Upload/Deep preview/Pricing page
- Regression checklist execution against staging
- Stripe test-mode checkout + webhook replay verification

### 6. Release/Ops Subagent
Owner: DevOps engineer

Deliverables:
- Stripe object setup in test/prod
- Env var rollout and secret validation
- Deployment execution in runbook order
- Production smoke + rollback readiness signoff

## Execution Order
1. Schema/Data + Billing/Stripe backend start in parallel
2. Pipeline/Worker starts as soon as billing contracts stabilize
3. Frontend starts once API contracts are in place
4. QA runs continuously; final full pass after all merges
5. Release/Ops executes rollout after QA signoff

## Gating Rules
- Do not deploy frontend before backend endpoints are live
- Do not deploy worker before migrations are applied
- Do not enable production webhook until backend webhook is deployed
- Block release if any of the 12 must-pass scenarios fail

## Daily Sync (15 min)
- Workstream status by owner
- Blockers and dependency handoffs
- Failed tests and owner assignment
- Go/No-go checklist progress
