# Deep Match Conversion Funnel (Current Behavior)

Last verified against code on 2026-03-10.

## What this funnel is
This funnel converts a completed **Quick Match** (`url_only`) run into either:
- a persuasive **Deep Match preview** (for free users), and then
- a paid **project unlock** that queues a full **Deep Match** (`content`) run.

## Stage 1: Upload entry and plan gating
1. `/api/process` accepts uploads and resolves pipeline type.
2. Free users default to `url_only` if no pipeline is provided.
3. If a free user explicitly requests `content`, API returns `403` with code `deep_match_requires_project_checkout`.
4. Paid users (agency/enterprise) can run `content` directly.

Implementation:
- `backend/routes/pipeline_routes.py` (`process_csv`)
- `frontend/src/components/UploadPage.tsx` (free-plan copy, pipeline picker)

## Stage 2: Quick Match completion triggers preview kickoff
1. Worker completes a non-preview `url_only` job.
2. Worker calls `DeepPreviewService.maybe_queue_preview(...)`.
3. If eligible, a **preview session** is created as a `content` job with `is_preview=true` and `source_session_id` set.
4. A snapshot row is upserted in `deep_match_previews` with `status='queued'`.

Implementation:
- `backend/worker.py` (`process_job`)
- `backend/services/deep_preview_service.py` (`maybe_queue_preview`)
- `database/migrations/020_add_deep_match_preview_funnel.sql`

## Stage 3: Preview eligibility filters
`maybe_queue_preview` short-circuits unless all are true:
1. `ENABLE_DEEP_MATCH_PREVIEW` is enabled.
2. Source session is `url_only` and not already a preview session.
3. User plan is `free`.
4. Project size meets minimum: `max(len(old_urls), len(new_urls)) >= DEEP_MATCH_BACKGROUND_MIN_PAGES` (default 50).
5. Embeddings are available (`OPENAI_API_KEY` present).
6. No existing preview in `queued|processing|completed` for this source.
7. User is under daily cap (`PREVIEW_MAX_JOBS_PER_USER_PER_DAY`, default 2 / 24h).
8. Candidate quality passes:
- at least 4 risky candidates,
- at least 4 candidate old URLs still present in source URLs,
- at least 2 selected new URLs for preview context.

Failure paths are persisted as `status='skipped'` with an `error_message`.

## Stage 4: Preview processing lifecycle
Stored states in `deep_match_previews` table:
- `queued`
- `processing`
- `completed`
- `failed`
- `skipped`

Route-level synthetic state:
- `not_applicable` (returned by API, not stored in table)

Transitions:
1. Worker marks preview session `processing` when it starts.
2. Worker marks `failed` on preview job error.
3. On preview completion, worker calls `finalize_preview(...)`.

Implementation:
- `backend/services/deep_preview_service.py` (`mark_processing`, `mark_failed`, `finalize_preview`)
- `src/redirx/database.py` (`DeepMatchPreviewDB`)

## Stage 5: Preview finalization rules (what becomes “convincing”)
For each old URL, a preview delta is shown only if all pass:
1. Deep target differs from Quick Match target.
2. Deep confidence `>= 0.86`.
3. Deep row is not `needs_review`.
4. Confidence margin (`deep - quick`) `>= 0.12`.
5. Deep top-vs-second similarity gap `>= 0.08`.

Then rows are ranked by conviction score (no hard cap).
If fewer than 3 convincing rows remain, preview is marked `skipped`.

If 3+ exist:
1. First `PREVIEW_FREE_ROWS` (default 2) are exposed in `visible_items`.
2. Remaining rows become blurred `locked_teasers`.
3. Snapshot status becomes `completed`.

Implementation:
- `backend/services/deep_preview_service.py` (`_build_convincing_fixes`, `_compute_deep_gaps`, `finalize_preview`)

## Stage 6: Review page conversion surfaces
For `url_only` review sessions:
1. **Unlock status panel** always appears (for any plan on url_only sessions).
2. **Deep preview card** appears only for free users.
3. Preview polling: every 4s while status is `queued|processing`.
4. Unlock-status polling: every 4s while quote is `checkout_created`, or unlocked deep session is `pending|processing`.

Key UI states:
- No quote: “View Project Pricing”.
- Quote exists but unpaid: “Unlock Deep Match”.
- `checkout_created`: waiting for payment confirmation.
- Paid + deep session completed: “Open Deep Match Results”.

Implementation:
- `frontend/src/components/ReviewInterface.tsx`
- `frontend/src/components/DeepMatchPreviewCard.tsx`
- `backend/routes/pipeline_routes.py` (`/api/results/<id>/deep-preview`, `/api/projects/<id>/unlock-status`)

## Stage 7: Quote and checkout (conversion transaction)
1. Pricing page is opened with `source_session_id`.
2. Backend creates/refreshes quote for the source Quick Match session.
3. Quote allowed only for source sessions that are:
- owned by user,
- not preview sessions,
- `pipeline_type='url_only'`,
- `status in {completed, permanently_failed}`.
4. Checkout endpoint creates Stripe Checkout session and sets quote status to `checkout_created`.

Implementation:
- `backend/services/pricing_service.py`
- `backend/routes/billing_routes.py` (`/api/pricing/quote`, `/api/billing/project/checkout`)
- `frontend/src/components/PricingPage.tsx`

## Stage 8: Payment webhook unlock and deep-run queueing
On Stripe `checkout.session.completed` for project checkout:
1. Quote is marked `paid` (idempotent event handling).
2. If quote has no `deep_session_id`, a full `content` deep session is created from the source session URLs.
3. `deep_session_id` is attached to the quote.

Implementation:
- `backend/services/stripe_service.py` (`_handle_project_checkout_completion`, `_queue_deep_session_for_quote`, webhook handlers)
- `backend/routes/billing_routes.py` (`/api/billing/webhook`)

## Stage 9: Post-payment completion
1. Unlock-status API reports `is_unlocked=true` once quote is `paid`.
2. UI watches `deep_session_status`.
3. When deep session reaches `completed`, user opens full Deep Match results from review panel.

Implementation:
- `backend/services/pricing_service.py` (`get_unlock_status`)
- `frontend/src/components/ReviewInterface.tsx`

## Backfill + self-healing behaviors
The deep-preview endpoint has resilience logic:
1. If source session is already complete and preview row is missing, route attempts on-demand backfill kickoff.
2. If snapshot says `queued|processing` but preview session is already `completed`, route finalizes preview on read.
3. If preview session permanently failed, route updates snapshot to `failed`.

Implementation:
- `backend/routes/pipeline_routes.py` (`get_deep_preview`)

## Analytics events used in this funnel
- Upload/entry: `quick_match_upload_started`
- Quick Match completion surface: `quick_match_completed`
- Preview lifecycle: `deep_preview_queued`, `deep_preview_ready`, `deep_preview_visible`
- Conversion clicks: `deep_preview_cta_primary_clicked`, `deep_preview_cta_secondary_clicked`, `deep_unlock_clicked_from_review`

Implementation:
- `frontend/src/components/UploadPage.tsx`
- `frontend/src/components/ReviewInterface.tsx`
- `frontend/src/components/DeepMatchPreviewCard.tsx`

## Config knobs that directly change funnel behavior
Defined in `src/redirx/config.py`:
- `ENABLE_DEEP_MATCH_PREVIEW`
- `DEEP_MATCH_BACKGROUND_MIN_PAGES` (default `50`)
- `PREVIEW_MAX_JOBS_PER_USER_PER_DAY` (default `2`)
- `PREVIEW_FREE_ROWS` (default `2`)
- `PREVIEW_MAX_OLD_CANDIDATES` (default `12`)
- `PREVIEW_MAX_NEW_URLS_FULL_SCAN` (default `300`)
- `PREVIEW_MAX_NEW_URLS_CAPPED` (default `180`)

## Funnel summary (compressed)
1. Free user uploads and runs Quick Match.
2. Worker may queue Deep Match preview in background.
3. Review page shows preview proof and lock CTA.
4. User opens pricing, checks out project unlock.
5. Webhook marks quote paid and queues full Deep Match session.
6. Review page updates unlock status and links to completed Deep Match results.
