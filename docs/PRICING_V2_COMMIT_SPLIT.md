# Pricing V2 Commit Split (Subagent Tracks)

Generated: March 6, 2026

## Important
- This repo currently has unrelated artifacts (benchmark/data/temp/doc files). Do **not** include those in pricing-v2 commits.
- Use the commit sequence below to keep review risk low.

## Track 1: Schema/Data Subagent
Commit message:
`feat(db): add pricing v2 schema and destructive legacy billing cleanup`

```bash
git add \
  database/migrations/021_pricing_v2_core.sql \
  database/migrations/022_pricing_v2_cleanup.sql
```

## Track 2: Billing/Stripe Backend Subagent
Commit message:
`feat(backend): implement pricing v2 quote, checkout, webhook, and billing status APIs`

```bash
git add \
  backend/app.py \
  backend/routes/billing_routes.py \
  backend/services/pricing_service.py \
  backend/services/stripe_service.py
```

## Track 3: Pipeline/Worker Subagent
Commit message:
`feat(pipeline): enforce free-vs-agency access model and agency metering`

```bash
git add \
  backend/routes/pipeline_routes.py \
  backend/services/deep_preview_service.py \
  backend/worker.py \
  src/redirx/config.py \
  src/redirx/database.py
```

## Track 4: Frontend Pricing/Billing Subagent
Commit message:
`feat(frontend): ship pricing page and replace legacy billing/trial/founder UI`

```bash
git add \
  frontend/src/App.tsx \
  frontend/src/api/billing.ts \
  frontend/src/api/pipeline.ts \
  frontend/src/api/user.ts \
  frontend/src/components/AccountPage.tsx \
  frontend/src/components/AuthCallback.tsx \
  frontend/src/components/DeepMatchPreviewCard.tsx \
  frontend/src/components/PricingPage.tsx \
  frontend/src/components/ReviewInterface.tsx \
  frontend/src/components/Settings.tsx \
  frontend/src/components/Sidebar.tsx \
  frontend/src/components/UploadPage.tsx \
  frontend/src/contexts/AuthContext.tsx \
  frontend/src/queries/queryKeys.ts \
  frontend/src/styles/globals.css \
  frontend/src/api/trials.ts \
  frontend/src/components/AdminOnboardingReport.tsx \
  frontend/src/components/AdminTrials.tsx \
  frontend/src/components/FounderLandingPage.tsx \
  frontend/src/components/FounderSuccessPage.tsx \
  frontend/src/components/TrialLandingPage.tsx
```

## Track 5: Testing/QA Subagent
Commit message:
`test: add pricing v2 backend/frontend/e2e coverage and webhook idempotency checks`

```bash
git add \
  backend/tests/test_billing_routes_v2.py \
  backend/tests/test_deep_preview_routes.py \
  backend/tests/test_error_transparency_routes.py \
  backend/tests/test_pricing_service.py \
  backend/tests/test_route_rate_limits.py \
  backend/tests/test_stripe_webhook_idempotency.py \
  backend/tests/test_upload_guards.py \
  backend/tests/test_worker_usage_accounting.py \
  frontend/package.json \
  frontend/package-lock.json \
  frontend/playwright.config.ts \
  frontend/src/api/pipeline.test.ts \
  frontend/src/components/DeepMatchPreviewCard.test.tsx \
  frontend/src/components/PricingPage.test.tsx \
  frontend/src/components/Settings.test.tsx \
  frontend/src/components/UploadPage.test.tsx \
  frontend/tests/e2e/pricing-overhaul.spec.ts
```

## Track 6: Release/Ops Subagent
Commit message:
`docs: add pricing v2 rollout runbook, pre-merge audit, and deployment handoff`

```bash
git add \
  .env.example \
  docs/pricing.md \
  docs/PRICING_V2_RELEASE_RUNBOOK.md \
  docs/PRICING_V2_SUBAGENT_ORCHESTRATION.md \
  docs/PRICING_V2_PREMERGE_AUDIT.md \
  docs/PRICING_V2_DEPLOYMENT_AGENT_HANDOFF.md
```

## Final Safety Check Before Push
```bash
git status --short
```
Confirm these are **not staged** unless intentionally included:
- `tests/.DS_Store`
- `data/`
- `results/`
- `docs/REDDIT_REDIRECT_BENCHMARK_PLAN.md`
- `scripts/reddit_benchmark/`
- `supabase/.temp/`
- ad-hoc CSV fixtures in repo root
