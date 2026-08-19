# Pricing V3 — paywall move outline

**Status:** proposal for review. Nothing below is built.
**Decision taken:** free Deep Match under a ~250-URL cap; paywall moves to export + scale; Watch subscription built alongside.

This outline exists to make the trade explicit before code moves, because the change inverts what the product charges for.

---

## 1. Current state (measured, not assumed)

| Fact | Value | Source |
|---|---|---|
| Users | 14, **all on `free`** | `user_profiles` |
| Sessions with URL data | 76 | `migration_sessions` |
| Median session size | **227 pages** | measured |
| Sessions ≤ 250 pages | **39 of 76 (51%)** | measured |
| Sessions ≤ 500 pages | **73 of 76 (96%)** | measured |
| p90 / max | 414 / 23,538 pages | measured |
| Deep Match price today | graduated, **$0.10/page** for first 500 | `pricing_service.py` |
| Embedding COGS | ~$0.02 per 1,000 pages | prior measurement |

**Today the paywall *is* Deep Match.** Quick Match runs free; Deep Match requires a per-project unlock (`requires_payment_unlock`), priced by graduated bands. A median 227-page job quotes ≈ **$22.70**.

Bands above 500 pages are close to theoretical at current usage — 96% of sessions never reach them.

---

## 2. What the change does

| | Today | Proposed |
|---|---|---|
| Quick Match | Free, unlimited | Free, unlimited *(unchanged)* |
| Deep Match run ≤250 URLs | **Paid** (~$25) | **Free** |
| Deep Match run >250 URLs | Paid, graduated | Paid, graduated *(unchanged)* |
| Viewing results on screen | Paid (behind unlock) | **Free** |
| Traffic-risk number | Free | Free *(unchanged)* |
| **Export** (CSV/.htaccess/Nginx/Vercel/WP) | Included once unlocked | **Paid — this becomes the gate** |
| Monitoring after launch | Does not exist | **New: Watch, ~$29/mo** |

### Value added to free
- A complete Deep Match run on a real site, not a 2-row preview.
- Full semantic results visible on screen, with the traffic-risk number.
- Effectively: the whole product, minus the artifact and minus scale.

### Value removed from free
- Export in any format. Currently free users cannot export anyway (they cannot reach Deep Match), so this is **not a takeaway from anyone today** — it is a takeaway from the *proposed* free tier.

### Value removed from paid
- The first ~250 pages stop generating revenue. At today's rate that is **~$25 × 51% of sessions**.

---

## 3. The cap is now a cost control, not a revenue lever

This is the important reframe. Once export is the paywall, the URL cap no longer decides *whether* you get paid — it decides how much free compute you hand out. So it should be set by what the worker can afford, not by what feels generous.

**Problem: we cannot currently measure that.** `migration_sessions` has `created_at` but **no `started_at`/`completed_at`**, so job duration is unmeasurable. Deep Match wall-clock is the real cost (embeddings are ~$0.005 for a 250-page job — noise).

> **Recommended prerequisite:** add `started_at` / `completed_at` to `migration_sessions` and let real jobs run for a week before fixing the cap number. Otherwise 250 is a guess.

**Also note:** 250 sits almost exactly on the median (227). Half of all sessions land within ~10% of the line, so small differences in site size flip users between free and paid. That is a fragile place to draw a boundary — either move it clearly below the median (e.g. 150) or clearly above (e.g. 400), unless "about half free" is the intent.

---

## 4. Architectural consequence — jobs now run before payment

Today `requires_payment_unlock` gates the **run**: nothing reaches the worker until Stripe confirms. Under V3, free Deep Match jobs execute first and payment happens after, at export.

That inverts the worker's risk profile:

- **Unpaid work becomes the default.** The queue fills with jobs that may never convert.
- **Free jobs can starve paying ones.** `WORKER_MAX_CONCURRENT` is 2. A burst of free 250-page jobs delays a paying customer's 5,000-page job behind them.
- **Abuse surface.** Free Deep Match with no per-user ceiling is an open compute faucet.

**Needed alongside the paywall move (not optional):**
1. **Queue priority** — paid jobs jump free jobs. `claim_next_job()` orders by created_at today; needs a priority column.
2. **Per-user free-run ceiling** — e.g. N free Deep Match runs per rolling window.
3. **Idempotency already helps** (duplicate CSVs reuse a session) but does not cap distinct sites.

---

## 5. Export as the gate — how solid is it?

Honest assessment: **moderately solid, deliberately leaky.**

- For 250 rows, on-screen results are technically transcribable. Anyone determined can copy them.
- That is probably acceptable and arguably good: the leak is laborious enough that the buyer of a real migration will pay, while the tire-kicker gets genuine value and tells people about it.
- What must *not* happen is making the on-screen result deliberately worse to protect the export. That would recreate the crippled-free-tier problem the funnel doc already flags as a conflict with product-led growth.

**Decision needed:** is export gated per project (one-time) or per subscription? Per-project fits the current `project_pricing_quotes` machinery and the one-migration-then-gone usage pattern. Per-subscription fits agencies.

---

## 6. Watch subscription (~$29/mo)

The foundation already exists, which is why this is cheap to build now:

- **`gsc_traffic_baselines` + `gsc_baseline_urls`** (migration 026) already snapshot a site's full traffic distribution at ingestion, keyed to the project, for every tier.
- **Email infrastructure** exists (Resend, templates, preferences, `email_log`).
- **GSC OAuth + refresh** exists and is in production publishing status.

**What Watch adds:**
1. Scheduled re-query of Search Console for a watched property (daily or weekly).
2. Diff against the stored baseline: which URLs lost traffic, which redirects now 404 or chain.
3. Alert email when a redirect breaks or a high-traffic URL drops beyond a threshold.
4. A persistent per-project view of the risk number over time.

**What it needs that does not exist:** a scheduler (cron service on Render — the email nudge cron is a working precedent), a redirect health-check pass, and threshold logic.

**Why it fits the pivot:** the pivot claim is "you sell a risk number, not a map." A one-time export is still a map. Watch is the risk number *as an ongoing service*, which is the only part of this that produces recurring revenue.

---

## 7. Implementation surface

Roughly ordered by dependency:

| # | Change | Files |
|---|---|---|
| 1 | Add `started_at`/`completed_at`, backfill nothing | migration + `worker.py` |
| 2 | Free-run eligibility (`pages <= FREE_DEEP_MATCH_PAGES`) | `pricing_service.py`, `job_limits.py` |
| 3 | Stop gating the run; gate export instead | `pipeline_routes.py`, `ReviewInterface.tsx`, `ExportModal.tsx` |
| 4 | Queue priority column + `claim_next_job()` update | migration, `worker.py` |
| 5 | Per-user free-run ceiling | `pricing_service.py` or new `usage_limits` |
| 6 | Quote/checkout keyed to export rather than unlock | `billing routes`, Stripe products |
| 7 | Pricing page + review-page copy rewrite | `PricingPage.tsx`, `ReviewInterface.tsx` |
| 8 | Watch: scheduler, diff, alerts, subscription plan | new service + cron + Stripe price |

Items 1–5 are the paywall move. 6–7 are the commercial surface. 8 is a separable product.

---

## 8. Open decisions for Dylon

1. **Cap number** — 250 (≈half free), or move it off the median? Recommend measuring job duration first.
2. **Export gate: per-project or per-subscription?**
3. **Free-run ceiling** — how many free Deep Match runs per user per month?
4. **Does the existing $349/mo Agency plan survive**, or does Watch replace it? Two subscriptions at $29 and $349 with unclear separation would be confusing.
5. **Sequencing** — paywall move and Watch together, or paywall first and Watch after it is stable?

---

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Free jobs starve paid jobs | High | Queue priority (item 4) — must ship with the change |
| Unbounded free compute | High | Per-user ceiling (item 5) |
| Cap set without cost data | Medium | Add timing columns first |
| Revenue drops before Watch lands | Medium | Sequence Watch close behind, or hold the cap lower initially |
| Cap sits on the median, feels arbitrary | Low | Move it clearly off 227 |
| On-screen results transcribable | Low | Accept; do not degrade the free experience to defend it |
