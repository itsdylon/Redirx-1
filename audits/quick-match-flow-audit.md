# Quick Match Flow Audit — PostHog-Aligned UX Overhaul

**Date:** 2026-03-15 (revised 2026-03-16)
**ICP:** Web developers or SEO specialists at small-to-mid agencies actively migrating a client's website who need to generate 301 redirects quickly without manual URL-by-URL matching.

---

## Executive Summary

The current Quick Match flow has strong bones — a genuinely useful free tool, a meaningful paid upgrade, and a clear technical distinction between the two. But the UX buries that clarity under competing CTAs, redundant warnings, and a conversion pipeline that asks users to understand internal concepts (project-scoped unlocks, idempotency keys, lease states) rather than simply guiding them from "I need redirects" to "I have better redirects."

This audit applies PostHog's product philosophy to identify what to change, what to remove, and what to simplify. Every recommendation cites the specific PostHog principle it draws from.

**Revision note:** This version incorporates the two-tool architecture (URL Based Matching and Content Based Matching as separate entry points), a cross-link from Quick Match review, and a results-first-then-paywall flow for Content Based Matching.

---

## Part 1: What's Working (Keep These)

**Free tier shows all results, ungated.** Your WS1 doc already resolved this correctly (previously implemented via plan @ /Users/dylonshattuck/Documents/Redirx-1/workstreams/WS1-conversion-funnel.md). Quick Match delivers complete value. This is the foundation of PostHog's free tier philosophy: hobbyists and pre-PMF teams should get real value for free, which creates word-of-mouth growth. ([PostHog pricing advice](https://newsletter.posthog.com/p/non-obvious-pricing-advice-for-startups))

**Confidence scores differentiate Quick from Deep honestly.** Showing "pattern-based ~70%" vs "content-aware ~99%" is a technical, truthful comparison — not a dark pattern. PostHog calls this letting the product sell itself by being visibly better. ([PostHog 50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building), #11: "Brand matters — product experience includes pricing.")

**Duplicate detection with a choice.** Giving users the option to view existing results or force a rerun respects their intent without being paternalistic.

**Export format variety.** Apache, Nginx, Cloudflare, Shopify, CSV, JSON, WordPress — this signals that you understand your ICP's actual deployment contexts.

---

## Part 2: Structural Change — Two Tools, Not Two Tiers

### The Reframe

The biggest change in this revision is treating URL Based Matching and Content Based Matching as **two distinct tools** rather than a free tier and a paid upgrade of the same thing. This aligns with PostHog principle #49: treat each product as its own mini-startup with its own pricing, revenue, and entry point. PostHog doesn't make you use Product Analytics first and then "upgrade" to Session Replay. They're separate tools in a nav dropdown. You pick the one that fits your problem. ([PostHog 50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building))

### Landing Page Tools Dropdown

The landing page gets a tools dropdown in the nav with two entries:

- **URL Based Matching** (free) → `/url-match` (current `/quick-match`, renamed)
- **Content Based Matching** → `/content-match` (new page)

Public-facing names describe *what the tool does*, not internal product tiers. A first-time visitor understands "URL Based" vs "Content Based" immediately. They don't know what "Quick" or "Deep" means in this context. PostHog's principle: "Your ICP defines everything" — agency devs think in terms of matching methodology, not tier names. ([PostHog handbook](https://posthog.com/handbook/who-we-are-building-for))

This also enables future tool expansion (broken link checker, sitemap validator from WS2) without architectural changes — the dropdown just gets more entries.

### Cross-Links Between Tools

Two cross-links connect the tools:

1. **Quick Match review → Content Based Matching.** Below the Quick Match results on the review page, a text CTA: *"Want better results? Try content based matching →"*. Links to `/content-match?source_session_id=X`, which pre-loads the user's files from the existing session so they don't re-upload. Price is calculated immediately from the known file sizes.

2. **Content Based Matching page → URL Based Matching.** On the Content Based Matching upload page, a text note: *"Just need URL pattern matching? Try our free URL based matching tool →"*. Links to `/url-match`. Serves users who landed on the wrong page or want to start free.

The post-Quick-Match conversion funnel (single upgrade prompt on review page) still exists as the primary conversion path. The cross-link CTA is secondary to it — it's a different route to the same destination for users who want to start fresh on the Content Based Matching page rather than upgrade in place.

---

## Part 3: What Needs to Change

### Problem 1: Two Primary CTAs at Upload Split Intent

**Current state:** Free users see both "Begin Quick Match →" and "Run Deep Match Immediately (Pay Later) →" once files are valid.

**Why this fails (PostHog lens):** PostHog's principle #25 says "Make ownership clear — reduces planning/meetings; enables faster shipping decisions." The same logic applies to user decisions. Two equally weighted CTAs create decision paralysis at the moment of highest intent. Your ICP — an agency dev mid-migration — doesn't want to evaluate pricing models at the upload screen. They want to match URLs.

PostHog's own approach: one primary action, always. Their free tier has one CTA: "Get started - free." Not "Get started free" AND "Start paid trial now." The paid path emerges *after* the user has experienced value.

**Recommendation:** The URL Based Matching upload page has one button: **"Match My URLs →"**. No secondary Deep Match CTA at all. Content Based Matching has its own page with its own upload and its own CTA. The tools dropdown and cross-links handle navigation between them. Decision paralysis eliminated at the source.

---

### Problem 2: Four Competing Unlock Surfaces on the Review Page

**Current state:** After Quick Match completes, the review page can show up to four separate conversion elements:
1. Pipeline type banner ("These results use Quick Match...")
2. Deep Match unlock panel (with 4+ sub-states: no quote, quote exists, checkout created, paid)
3. Deep Match preview card (with its own multi-state flow: awaiting opt-in → processing → completed → failed)
4. Locked results banner (for Path B direct-deep users)

**Why this fails (PostHog lens):** PostHog principle #10 says "Map all possibilities" for planning — but that doesn't mean *show* all possibilities to the user simultaneously. PostHog's pricing page has exactly one flow: see price → click "Get started" → done. They explicitly ditched multi-path enterprise pricing because "[transparent pricing is] fundamentally better for both the company and customers." ([PostHog transparent pricing](https://posthog.com/blog/transparent-enterprise-pricing))

Four surfaces competing for attention violates another PostHog principle: "Replace simplicity with transparency." Transparency means the user understands exactly what's happening and what to do next. Four CTAs with different backend states is complexity, not transparency.

**Recommendation:** Collapse all upgrade messaging on the Quick Match review page into **one persistent, state-aware component** at the top. It has exactly one CTA at any given time:

| User state | What they see | Single CTA |
|---|---|---|
| Quick Match complete, no upgrade started | "URL matching found X redirects at ~Y% avg confidence. Content based matching uses page analysis to improve low-confidence results." | "See Content Match pricing for this project →" |
| Quote generated, unpaid | "Content Based Matching for this project: $Z (one-time)" | "Purchase Content Match — $Z" |
| Payment processing | "Payment received. Content match is running..." | (progress indicator, no CTA) |
| Content Match complete | "Content match results are ready." | "View Content Match Results →" |

One component. One CTA. Always clear what to do next. Kill the preview card, kill the separate banner, kill the separate panel. Merge them.

Below this component, the secondary text CTA: *"Want better results? Try content based matching →"* links to `/content-match?source_session_id=X` as an alternative path for users who prefer to start fresh on the dedicated page.

---

### Problem 3: The Deep Match Preview Funnel Is Overcomplicated

**Current state:** The preview card goes through: `awaiting_opt_in → queued → processing → completed (with visible + blurred locked teasers) → failed/skipped`. It requires a second scraping acknowledgment. On completion, it shows "mistake prevented" rows plus blurred locked teasers with a pricing CTA.

**Why this fails (PostHog lens):** PostHog's activation metric research ([PostHog activation](https://newsletter.posthog.com/p/wtf-is-activation-and-why-should)) found that the activation event must capture when actions *complete*, not when they start. The preview funnel creates a micro-activation loop (opt-in → wait → see teaser → paywall) that can actually *hurt* conversion if the preview fails or shows "unavailable for this run." A failed preview is worse than no preview because it creates negative signal about Deep Match quality.

**Recommendation:** Delete the preview card entirely. Its job is replaced by two things:

1. The single upgrade prompt on Quick Match review (Problem 2 solution) — provides the conversion CTA.
2. The Content Based Matching results-first-then-paywall flow (see Part 4) — provides the "proof before purchase" that the preview card was trying to deliver, but does it with real results rather than a fragile teaser pipeline.

---

### Problem 4: Scraping Defense Warning Appears Twice

**Current state:** Users see the scraping defense checklist at upload (if selecting Deep Match) AND again in the preview card opt-in on the review page.

**Why this fails (PostHog lens):** PostHog principle #17 says "Ship fast, iterate — waiting weeks kills momentum." The same applies to user momentum. Asking someone to acknowledge the same warning twice signals that either the product doesn't trust them or the product doesn't trust itself.

**Recommendation:** Show the scraping defense warning exactly **once**, on the Content Based Matching page, right before the run starts. This is the only place where scraping actually happens, and it's the natural moment for the user to prepare their sites. The warning no longer appears on the URL Based Matching upload page (no scraping involved) or on the Quick Match review page (no scraping initiated from there).

For the in-place upgrade path from Quick Match review, the scraping consent appears as part of the upgrade prompt when the user clicks "Purchase Content Match" — one modal, one acknowledgment, one time.

---

### Problem 5: Project-Scoped Pricing Requires a Source Session ID

**Current state:** Free users can only access the pricing page with a `source_session_id` parameter. Generic `/pricing` is blocked for tool users. Without the parameter, they see "No project selected."

**Why this fails (PostHog lens):** PostHog explicitly killed "talk to sales" barriers and made all pricing publicly visible because hiding prices "[creates] friction that drives away exactly the high-intent buyers you want." ([PostHog transparent pricing](https://posthog.com/blog/transparent-enterprise-pricing)) Requiring a session ID to even *see* pricing is the structural equivalent of "talk to sales."

**Recommendation:** Make pricing visible without a session ID. The Content Based Matching page itself shows pricing — either a calculator based on URL count (before upload) or an exact quote (after upload). A standalone `/pricing` page also exists for inbound traffic from blog posts, Reddit, and referrals, showing the pricing table and a CTA to start. No dead ends.

---

### Problem 6: The Upload Page Has Too Much Explanatory UI

**Current state:** The upload page shows: a headline, a subheading, a Quick Match Info Banner with 3-step visual cards, a pipeline type selector (for non-free users), two file upload zones, validation warnings, scraping defense warnings, and two CTA buttons with sub-messages.

**Why this fails (PostHog lens):** PostHog principle #22: "No design by default." Your ICP is a web dev or SEO specialist — they understand what "upload a CSV" means. The 3-step visual cards and explanatory banners are solving a problem that doesn't exist for your audience.

**Recommendation:** Strip both upload pages to essentials:

**URL Based Matching (`/url-match`):**
1. Headline: "URL Based Redirect Matching"
2. One-liner: "Upload two sitemaps. Get matched redirects in seconds."
3. Two file drop zones: "Old Site URLs" / "New Site URLs"
4. One button: "Match My URLs →"
5. Small expandable "How it works" for curious users

**Content Based Matching (`/content-match`):**
1. Headline: "Content Based Redirect Matching"
2. One-liner: "We scrape and analyze your pages to match by actual content — not just URLs."
3. Two file drop zones: "Old Site URLs" / "New Site URLs"
4. Pricing shown after upload (calculated from file sizes)
5. Scraping consent checklist (one time, here)
6. One button: "Start Content Match →"
7. Small note: "Results are free to preview. Payment required to export."

Each page owns exactly one tool. No pipeline selector, no tier comparison, no competing CTAs.

---

### Problem 7: "Unlock" Language Is Overloaded

**Current state:** "Unlock Deep Match" appears in the review banner, the unlock panel, the preview card, and the pricing page — each time with a different backend meaning.

**Why this fails (PostHog lens):** PostHog's pricing advice says "How you price is who you are." When "Unlock" means four different things, users learn to distrust it.

**Recommendation:** The word "Unlock" disappears entirely. CTAs are specific to what happens next:

| Context | CTA |
|---|---|
| Quick Match review, upgrade prompt | "See Content Match pricing for this project" |
| Quick Match review, quote ready | "Purchase Content Match — $X" |
| Content Match results, pre-payment | "Purchase to export — $X" |
| Content Match results, paid | "Export Results" |
| Cross-link from Quick Match review | "Want better results? Try content based matching →" |

Every CTA tells the user exactly what clicking does.

---

### Problem 8: The Loading Screen Doesn't Build Anticipation

**Current state:** Loading screen shows step progress, a spinner, and a "Continue in background" button.

**Recommendation (aligns with WS1 #3):** Use the loading screen to educate about what Content Based Matching does differently. Not as a sales pitch — as genuine education:

- "Matching by URL patterns... Content matching also compares page titles and body text"
- "Found 3 exact URL matches so far... Content matching catches pages that moved to entirely different paths"
- "URL matching is great for straightforward migrations. Content matching handles redesigns where URLs change completely"

This primes the user to understand the upgrade value *before* they see results, making the conversion prompt on the review page more effective.

---

## Part 4: Content Based Matching — Results-First-Then-Paywall Flow

This is the core design for the Content Based Matching tool's dedicated page. The key principle: **let users see proof before asking for money.**

PostHog's free tier philosophy applied to a paid tool: you don't need to make the whole product free, but you do need to show enough value that the purchase decision is obvious. PostHog's activation research says the activation event is when a user "experiences your product's value for the first time." For Content Based Matching, that moment is seeing the summary stats and blurred results — not the upload, not the consent, not the loading screen. ([PostHog activation](https://newsletter.posthog.com/p/wtf-is-activation-and-why-should))

### The Flow

```
1. LAND on /content-match
   → From tools dropdown, cross-link from Quick Match review,
     or direct URL (blog post, referral, etc.)
   → If arriving with ?source_session_id=X, files are pre-loaded

2. UPLOAD two files (or see pre-loaded files)
   → Price shown immediately after upload, calculated from file sizes
   → Scraping consent checklist (one time, right here)
   → One button: "Start Content Match →"

3. PROCESSING screen
   → "Content matching takes longer because we're scraping and analyzing
     your actual pages. Estimated time: X minutes."
   → Real-time progress: pages scraped, embeddings generated
   → "Continue in background" option
   → Playful, honest copy (per WS1): own the wait time, don't hide it

4. RESULTS arrive on /review/:sessionId
   → Page renders in PREVIEW MODE (pre-payment):
     - Summary stats visible: total matches, avg confidence,
       confidence distribution, exact match count
     - Table rows show REDACTED data: old URL truncated
       (first few chars + "..."), new URL hidden entirely
     - Visual blur overlay on the table as polish
       (but actual data is redacted server-side — not bypassable)
     - Confidence badges visible per row (user can see the
       distribution of high/medium/low matches)
   → Paywall prompt above the table:
     "Content Match found X redirects at Y% avg confidence.
      Purchase to view and export full results."
     [Purchase full results — $Z]

5. PAY via Stripe
   → Inline checkout or redirect to Stripe, returns to same page

6. RESULTS UNLOCK
   → Same /review/:sessionId, now with full data
   → Table shows complete old/new URLs, alternatives, similarities
   → Export enabled
   → Same review interface as URL Based Matching results,
     but with richer columns (title similarity, content similarity,
     alternative suggestions)
```

### Why Results-First-Then-Paywall Works

**Server-side redaction, not client-side blur.** The API returns two response shapes for Content Match results:

- **Pre-payment:** `GET /api/results/:sessionId` returns a preview payload — summary statistics (match count, average confidence, confidence distribution) plus truncated row data (first 3 chars of old URL, no new URL, confidence badge only). The full mapping data is never sent to the client.

- **Post-payment:** Same endpoint returns the full payload with complete URLs, similarity scores, and alternative suggestions.

The CSS blur on the table is cosmetic — it signals "there's data here you'll get access to" — but the actual security is that the data was never transmitted. This pattern is standard in SaaS tools like Ahrefs and SEMrush. A user inspecting dev tools sees truncated strings, not the real mappings.

**Sunk cost + proof = high conversion.** By the time the user sees the paywall, they've uploaded files, consented to scraping, waited for processing, and can see that the tool produced results with specific confidence scores. The purchase decision is backed by evidence, not a promise. PostHog's principle: "Build to validate — ideas cannot be validated pre-launch; markets validate actual products." Here the user validates the product's output before purchasing. ([PostHog 50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building), #15)

**One active unpaid run per user (existing guardrail).** The current `direct-deep` path already enforces this. Keep it — it prevents abuse of the free preview while keeping the flow generous. Stale runs auto-expire after 72 hours (existing behavior).

### Cross-Link Pre-Loading

When a user arrives at `/content-match?source_session_id=X` from the Quick Match review page:

1. Frontend fetches the existing session's file metadata
2. Upload zones show pre-loaded files (filename + row count) with an option to change them
3. Price is calculated and shown immediately
4. Scraping consent is the only step before "Start Content Match →"

This removes the friction of re-uploading while keeping the Content Based Matching page as the owner of its own flow. The user still sees the price, still consents to scraping, and still explicitly starts the run. No steps are skipped — but the boring ones (file upload) are pre-filled.

---

## Part 5: Revised Complete User Journeys

### Journey A: Start Free, Upgrade Later (Primary Conversion Path)

```
1. Land on site → Tools dropdown → "URL Based Matching (free)"
2. /url-match: Upload files → "Match My URLs →"
3. Loading screen with educational messaging
4. /review/:id: Full URL match results visible
   → Single upgrade prompt at top: "See Content Match pricing →"
   → Text CTA below results: "Want better results?
     Try content based matching →"
5a. Click upgrade prompt → inline quote → "Purchase Content Match — $X"
    → Scraping consent (once) → Pay → Content Match runs
    → Results appear on same review page with richer data
5b. Click text CTA → /content-match?source_session_id=X
    → Files pre-loaded → Price shown → Consent → Run → Preview results
    → Pay → Full results
```

### Journey B: Start with Content Match (Power User / Returning User)

```
1. Land on site → Tools dropdown → "Content Based Matching"
2. /content-match: Upload files → See price → Consent → "Start Content Match →"
3. Processing screen (honest about scraping time)
4. /review/:id in preview mode:
   → Summary stats visible, table data redacted/blurred
   → "Purchase full results — $X"
5. Pay via Stripe
6. Full results visible, export enabled
```

### Journey C: Inbound from Blog/Reddit/Referral

```
1. Land on /content-match directly (or /pricing → CTA → /content-match)
2. Same as Journey B from step 2
```

### Journey D: Agency/Enterprise User

```
1. Land on /dashboard (existing behavior)
2. /upload with tool selector toggle (not two competing cards)
3. Run either tool directly, no payment gates
4. Results on /review/:id with full data
```

---

## Part 6: PostHog Principles Referenced

| Principle | Source | How It Applies |
|---|---|---|
| Generous free tier creates word-of-mouth | [Pricing advice](https://newsletter.posthog.com/p/non-obvious-pricing-advice-for-startups) | URL Based Matching shows all results, ungated |
| Treat each product as a mini-startup | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #49 | Two tools with separate entry points, not tiers of one tool |
| "How you price is who you are" | [Pricing advice](https://newsletter.posthog.com/p/non-obvious-pricing-advice-for-startups) | CTAs are specific actions, "Unlock" eliminated |
| Transparent pricing over "talk to sales" | [PostHog blog](https://posthog.com/blog/transparent-enterprise-pricing) | Pricing visible on Content Match page and standalone /pricing |
| One activation metric, upstream of everything | [Activation newsletter](https://newsletter.posthog.com/p/wtf-is-activation-and-why-should) | Activation = seeing Content Match preview results |
| Build to validate | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #15 | Users see results before purchasing (results-first paywall) |
| Ship fast, iterate | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #17 | Delete preview card complexity, ship static redacted preview |
| Make ownership clear | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #25 | One button per screen, one decision at a time |
| Product is the growth engine | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #11 | Confidence scores and summary stats sell Content Match |
| ICP defines everything | [PostHog handbook](https://posthog.com/handbook/who-we-are-building-for) | Agency devs don't need explanatory UI; tool names describe methodology |
| No design by default | [50 learnings](https://newsletter.posthog.com/p/50-things-weve-learned-about-building) #22 | Stripped upload pages, no 3-step visual cards |

---

## Part 7: Implementation Priority

Ordered by impact-to-effort ratio, using PostHog's growth engineering framework (target area → metric → hypothesis → small experiment):

### Phase 1: Quick Wins (1-2 days total)

1. **Remove secondary "Pay Later" CTA from upload page** (1 hour) — Zero risk, immediately reduces decision paralysis. Just delete the button and its handler.

2. **Replace all "Unlock" language with specific CTAs** (1-2 hours) — Copy changes only across DeepMatchPrompt, ReviewInterface, PricingPage. Builds trust at every touchpoint.

3. **Collapse four upgrade surfaces into one component** (1-2 days) — Refactor DeepMatchPrompt to be the single state-aware upgrade component on review. Delete the preview card component, the separate banner, and the separate panel. Highest impact on north star metric (Quick Match → pricing click-through).

### Phase 2: Content Based Matching Page (3-5 days)

4. **Create `/content-match` route and page** (2-3 days) — New page with upload zones, inline pricing, scraping consent, and "Start Content Match →" CTA. Handles both cold start (empty upload) and warm start (pre-loaded from `source_session_id`).

5. **Build results-first paywall on review page** (2-3 days) — API returns preview payload (summary stats + truncated rows) for unpaid Content Match sessions. Review page renders preview mode with redacted table and paywall prompt. Post-payment, same endpoint returns full data.

### Phase 3: Polish and Connect (2-3 days)

6. **Add tools dropdown to landing page nav** (half day) — Two entries: "URL Based Matching (free)" and "Content Based Matching". Links to respective pages.

7. **Add cross-link CTA to Quick Match review** (half day) — Text link below results: "Want better results? Try content based matching →" linking to `/content-match?source_session_id=X`.

8. **Strip upload pages to essentials** (1 day) — Remove 3-step visual cards, info banners, pipeline selector from URL Based Matching. Content Based Matching page is already clean by design.

9. **Add educational loading screen messages** (1 day) — Rotating tips about Content Based Matching during URL Based Matching processing.

10. **Make pricing visible without session ID** (1 day) — Pricing calculator on `/content-match` (before upload) and standalone `/pricing` page for inbound traffic.

### Phase 4: Cleanup (1 day)

11. **Delete dead code** — Remove DeepMatchPreviewCard component, old preview API endpoints, duplicate scraping warning states, `showScrapingWarning` / `scrapingWarningAcknowledged` booleans from UploadPage, old "Pay Later" handler and route.

12. **Update tests** — Remove tests for double consent (UploadPage.test.tsx line 382, PricingPage.test.tsx line 143). Add tests for preview payload redaction, cross-link pre-loading, single consent flow.

---

## Part 8: Measurement

Per PostHog's framework, track these metrics in order of priority:

**Primary (north star):** URL Based Match completion → Content Match purchase rate. This is the end-to-end conversion metric. Everything above is designed to increase it.

**Activation metrics:**
- URL Based Matching: Upload → results viewed (free tool activation)
- Content Based Matching: Upload → preview results seen (pre-purchase activation)

**Funnel metrics:**
- Quick Match review → pricing prompt click-through rate
- Quick Match review → cross-link click-through rate (secondary path)
- Content Match preview → purchase conversion rate
- Content Match purchase → export rate (validates paid users get value)

**Health metrics:**
- Time from URL Match upload to results (should stay fast)
- Time from Content Match start to preview results (set expectations)
- Content Match scraping success rate (monitor for WAF-blocked runs)
- One-active-unpaid-run guardrail trigger rate (are users hitting limits?)

**Metric to retire:** The old "Quick Match → Deep Match click-through rate" as a single number. Replace with the two-path breakdown (upgrade prompt vs. cross-link) to understand which conversion path performs better.
