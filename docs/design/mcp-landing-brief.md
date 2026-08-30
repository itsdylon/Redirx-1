# MCP Landing Page — Content + Visual Direction Brief

**For:** the agent building the page. **Status of the product:** built and tested on
`feat/mcp-server` (65 commits ahead of `main`), **not merged, not deployed**. Every
claim below was verified against code on 2026-08-30 — see §8 for the three places
the code is narrower than the pitch.

**Do not write copy that implies a live one-click OAuth login.** It does not exist
(§8.1). The working connect story is an API key as a bearer token.

---

## 1. Brief inference (what this page has to earn)

| | |
|---|---|
| **Domain** | Developer infrastructure — SEO migration tooling, now agent-drivable |
| **Audience** | Technically literate agency owners, solo devs, and in-house SEO leads who already run a coding agent. Skeptical of AI hype; burned by tools that gate the good part. |
| **Mood adjective the result must earn** | **Trustworthy.** Not exciting, not futuristic. The page should read like documentation you'd act on. |
| **Anti-goal** | Anything that reads as a dashboard, a dark "AI product," or a demo reel. No glowing gradients, no terminal-green, no looping animations. |
| **Motion depth** | Restrained. Entrance fades and one sequenced reveal. Nothing loops. |
| **Layout sequence** | Left-aligned asymmetric hero + artifact card → tool table → full-bleed connect band → alternating claim/visual rows → narrow centered FAQ → quiet closing CTA |

**Why the page can be this plain:** the product's actual differentiator is a
business-model claim (quality is never gated; only the file is paid). Loud design
undercuts a trust argument. Restraint *is* the pitch.

---

## 2. Resolved token system

### 2.1 Accent — one color, used everywhere

**`#0B6E77` — deep signal teal.**

> **Justification (one line):** teal reads as *routing/signal confirmed* rather than
> alarm or generic SaaS-blue, and it is distinct from both reference sites
> (humblytics orange, paid.ai forest green) while staying legible at AA on a warm
> white ground.

The existing app has no brand hue to honor — `--primary` is `#030213`, `--accent`
is `#e9ebef`, i.e. effectively monochrome. This page establishes the accent.

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `--accent-700` | `#0A6068` | Accent **text** on light ground, button hover/pressed | 6.98:1 on `--bg` ✅ AA |
| `--accent-600` | `#0B6E77` | **Primary button fill** (white text), active underlines | 5.98:1 vs white ✅ AA |
| `--accent-500` | `#0E7C86` | Icons, hairline rules, diagram strokes | 4.95:1 vs white ✅ AA |
| `--accent-100` | `#CFE9EC` | Borders on tinted cards, sequence connector lines | decorative |
| `--accent-050` | `#EAF6F7` | Badge/chip fills, "Free" chip background | decorative |

**Single-accent discipline:** there is no second hue. The paid/free distinction in
the tool table is **accent tint chip (free) vs. ink outline chip (paid)** — not
green/red. Do not introduce a semantic color palette.

### 2.2 Neutrals

| Token | Hex | Use | Contrast on `--bg` |
|---|---|---|---|
| `--bg` | `#FBFAF8` | Page ground (warm paper white, not pure `#fff`) | — |
| `--surface` | `#FFFFFF` | Cards, so they lift off the ground | — |
| `--hairline` | `#E8E6E1` | 1px card/section borders — these do most of the work | — |
| `--ink-900` | `#14171A` | Headlines | 17.25:1 ✅ AAA |
| `--ink-700` | `#3D444B` | Body copy, subheads | 9.47:1 ✅ AAA |
| `--ink-500` | `#636C75` | Eyebrow labels, captions, muted meta | 5.12:1 ✅ AA |

`--ink-500` is for text ≥14px only. Do not use it below that.

### 2.3 Typography

- **Headings:** `Inter Tight` (Google Fonts), weight 600. Fallback:
  `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
- **Body:** `Inter` 400/500. Same fallback stack.
- **Mono:** `JetBrains Mono` 400/500 — used for the connect command, tool names,
  and field names. Fallback: `ui-monospace, SFMono-Regular, Menlo, monospace`

| Role | Size | Line-height | Tracking | Weight |
|---|---|---|---|---|
| Hero H1 | `clamp(2.5rem, 1.6rem + 3.2vw, 4rem)` | 1.05 | -0.03em | 600 |
| Section H2 | `clamp(1.75rem, 1.3rem + 1.6vw, 2.5rem)` | 1.15 | -0.02em | 600 |
| Card H3 | `1.25rem` | 1.3 | -0.01em | 600 |
| Lead/subhead | `clamp(1.0625rem, 1rem + 0.4vw, 1.25rem)` | 1.55 | 0 | 400 |
| Body | `1rem` | 1.6 | 0 | 400 |
| Eyebrow label | `0.8125rem` | 1.45 | **0.06em, uppercase** | 500 |
| Mono | `0.875rem` | 1.6 | 0 | 400 |

**Measure:** body max `68ch`; hero subhead max `52ch`. Never full-bleed text.

### 2.4 Spacing, radius, elevation

- **Base 8px.** Scale: `4, 8, 12, 16, 24, 32, 48, 64, 96, 128`.
- **Section rhythm:** `96px` vertical padding mobile, `128px` desktop.
- **Container:** max-width `1120px`, gutter `24px`.
- **Radius:** `--r-sm: 6px` (chips) · `--r-md: 10px` (buttons) · `--r-lg: 16px`
  (cards) · `--r-xl: 24px` (hero artifact card, feature visual frames) ·
  `--r-pill: 999px` (eyebrow badge).
- **Shadow** (soft and low — borders carry the structure):
  - `--shadow-card: 0 1px 2px rgba(20,23,26,.04), 0 8px 24px -12px rgba(20,23,26,.10)`
  - `--shadow-lift: 0 2px 4px rgba(20,23,26,.05), 0 20px 48px -20px rgba(20,23,26,.16)`

### 2.5 Motion

- **Durations:** `160ms` state/hover · `240ms` entrance · `400ms` per sequence step.
- **Easing:** entrances `cubic-bezier(.22,.61,.36,1)`; state changes `cubic-bezier(.4,0,.2,1)`.
- **Entrances:** `opacity 0→1` + `translateY(12px→0)`, staggered `60ms` per child,
  fired by IntersectionObserver at 20% visibility, **once** — never re-trigger.
- **The tool-sequence card (§4.2):** steps reveal at `400ms` intervals on first
  view, then hold. **It does not loop.** A looping animation beside a payment claim
  reads as a sizzle reel, and this page's argument is honesty.
- **`prefers-reduced-motion: reduce`:** all entrances become instant `opacity: 1`,
  no transform; the sequence renders fully revealed on mount.

---

## 3. Hero

**Eyebrow badge (pill, `--accent-050` fill, `--accent-700` text):**
`Introducing the Redirx MCP server`

### Headline options — pick one

1. **Your agent can now do the migration.** / *Redirx as four tools, not a web app.*
2. **Point your agent at the old site. Get the redirect file.**
3. **Redirect mapping your coding agent can actually run.**

> Recommendation: **#2.** It states the whole job in one breath and is the only
> option that names both ends of the workflow. #1 is the safe fallback.

### Subhead options

1. Four MCP tools — discover, deep match, preview, export. The matching engine runs
   free at full quality on every plan. You pay for the deploy-ready file, and only
   when you want it.
2. Redirx's content-matching engine is now a remote MCP server. Your agent
   enumerates both sites, runs the full matcher, and reads the results back — all
   free. The redirect file is the only paid step.

> Recommendation: **#1** — leads with the tool names, which is what a technical
> reader scans for.

### CTAs

- **Primary** (`--accent-600` fill, white text, `--r-md`): `Get an API key`
  → links to `/api-keys`
- **Secondary** (ghost, `--hairline` border, `--ink-900` text): `See the four tools`
  → anchor to §4

Under the CTAs, `--ink-500`, `0.8125rem`:
`Streamable HTTP. No npx, no local binary, nothing to install.`

### Hero visual

Right-hand **artifact card** (`--surface`, `--r-xl`, `--shadow-lift`, `1px --hairline`),
left-aligned hero text at ~52% width. The card shows an abbreviated agent transcript
— **static, styled, not a video**:

```
→ discover  domain: "oldshop.com", side: "old"
  Found 412 URLs on https://oldshop.com via sitemap.

→ deep_match  old_urls: [412], new_urls: [389]
  Started migration 7f3a… (status: pending)

→ deep_match  migration_id: "7f3a…"
  Migration 7f3a…: completed (done)
  Total matches: 397.

→ preview  migration_id: "7f3a…"
  397 matches (312 high, 61 medium, 24 low; 44 flagged for review).
```

Use real field names (`domain`, `side`, `old_urls`, `migration_id`) — they are
verbatim from the tool schemas. Numbers are illustrative; label them as an example
in a caption so nothing reads as a customer metric.

---

## 4. Section-by-section outline

### 4.1 — Section 1: The four tools

**Purpose:** answer "what does it actually do" in one screen, and land the pricing
inversion immediately rather than saving it for a pricing page.

**Eyebrow:** `THE TOOLSET`
**H2:** `Four tools. Only one of them is paid.`
**Lead:** `Matching quality is never gated. Paying doesn't change what was matched — it unlocks the file you install.`

**Copy — a 4-row table or 2×2 card grid:**

| Tool | Copy | Chip |
|---|---|---|
| `discover` | Enumerates a site's page URLs from a root domain — sitemap first, then the CMS's own API (WordPress, Shopify), then a crawl. Seconds, not minutes. Run it once for the old site, once for the new. | Free |
| `deep_match` | The full content-matching engine. Scrapes and compares actual page content, not just URL similarity. Runs on your full URL set, at full quality, on every plan. Starts a background run and returns a `migration_id` you poll. | Free |
| `preview` | Match count, a high/medium/low confidence breakdown, how many old URLs found nothing, and ~20 sample pairings — deliberately half from the *bottom* of the confidence range, not a highlight reel. | Free |
| `export` | The deploy-ready redirect file: Apache, nginx, WordPress, Vercel, Cloudflare, Shopify, CSV, or JSON. Filter by minimum confidence, choose paths or absolute URLs. | Paid |

**Visual:** 2×2 grid of cards (`--surface`, `--r-lg`, `1px --hairline`,
`--shadow-card`). Tool name in **mono**, `--accent-700`. The Free chip is
`--accent-050` fill / `--accent-700` text; the Paid chip is transparent with a
`1px --ink-900` border and `--ink-900` text. The `export` card gets a slightly
heavier border to carry the emphasis without a second color.

---

### 4.2 — Section 2: The sequence

**Purpose:** show the four tools as one workflow, so the reader understands this is
a pipeline and not a menu.

**Eyebrow:** `HOW A RUN GOES`
**H2:** `Discover, match, look, then decide whether to pay.`
**Lead:** `Every step before the file is free — which means your agent can find out whether the match is any good before anyone spends anything.`

**Copy — four numbered steps:**

1. **Discover both sides.** `discover` twice — once with `side: "old"`, once with
   `side: "new"`. Its `urls` output is exactly `deep_match`'s input.
2. **Start the match.** `deep_match` with `old_urls` and `new_urls` returns a
   `migration_id` immediately. A real site takes minutes — the engine is scraping
   every page — so the agent polls the same tool with just the `migration_id` until
   `done` is true.
3. **Read the result.** `preview` returns aggregates and a sample weighted toward
   the *shaky* matches, so nobody approves a file on the strength of its best rows.
4. **Export.** `export` returns the redirect file in your target format. This is the
   paid step, and the first one.

**Visual:** the **sequenced reveal card** (this is the page's one animated moment).
A vertical connector line in `--accent-100` links four steps; each step is a mono
tool-call line plus a plain-language result line. Steps reveal at 400ms intervals on
first scroll into view, then hold. Real field names throughout: `side`, `old_urls`,
`new_urls`, `migration_id`, `done`.

---

### 4.3 — Section 3: Connect

**Purpose:** remove the "how hard is setup" objection, and be honest that the
current path is an API key.

**Eyebrow:** `CONNECTING`
**H2:** `Two steps. Nothing to install.`
**Lead:** `It's a remote server over Streamable HTTP — no stdio wrapper, no npx, no local binary. Your client connects to a URL.`

**Copy + the literal block (see §5 for exact content).**

**Visual:** full-bleed band in `--surface` against the `--bg` page, with a
copyable command block: `--ink-900` background, mono, `--r-lg`, a copy button in
`--accent-600`. This is the one place a dark surface is allowed — it's a terminal,
and readers expect it.

---

### 4.4 — Section 4: Why quality isn't the paywall

**Purpose:** the actual differentiator. This is the section that should convince.

**Eyebrow:** `THE MODEL`
**H2:** `Gating the match quality made sense for humans. It doesn't for agents.`

**Copy:**

> Most tools cripple the free tier and hope you upgrade to find out whether it
> worked. That leverage disappears when the customer is an agent: a degraded run
> isn't a teaser, it's just bad data your agent will reason from and act on.
>
> So Redirx inverted it. `deep_match` runs the same engine, on your full URL set,
> at full quality, regardless of plan. `preview` shows you the weak matches on
> purpose. You decide to pay once you've already seen exactly what you'd be paying
> for.
>
> The one thing we ration is *volume*, not quality: free accounts get a limited
> number of Deep Match runs in a rolling 24-hour window. Hitting that returns a
> clear "retry later," never a worse result.

**Visual:** a comparison card, two columns inside one frame — "What's usually
free / What's free here" — with the row labels *Full URL set*, *Full matching
quality*, *Confidence breakdown*, *Sample of weak matches*, *Deploy-ready file*.
Marks in `--accent-600`; absences as a neutral `--ink-500` dash. No red X's.

---

### 4.5 — Section 5: Payment, and what your agent cannot do

**Purpose:** the trust section. An agent-wary visitor's first fear is autonomous
spending. Answer it before the FAQ.

**Eyebrow:** `PAYMENT`
**H2:** `Your agent asks. A human pays.`

**Copy:**

> When `export` needs payment, it returns a structured Payment Required response —
> MPP, JSON-RPC error `-32042` — carrying a checkout URL. That URL has to be opened
> by a person in a browser. The agent cannot complete the payment, by design: this
> is Stripe Checkout with a human in the loop, not an autonomous
> agent-holds-a-card flow.
>
> After payment, the agent just calls `export` again. There's no "confirm payment"
> step to get wrong — the tool re-checks entitlement on every single call, so
> retrying with the same arguments always works.

**Visual:** a two-panel card. Left: the structured error, rendered as a small mono
block showing `code: -32042` and a `checkoutUrl` field. Right: a plain sentence and
a lock/browser glyph in `--accent-500` captioned `Opened by you, in your browser.`
Understated — no Stripe logo, no fake checkout UI.

---

### 4.6 — Closing CTA

**H2:** `Get a key and point your agent at it.`
**Lead:** `Free to connect. Free to discover, match, and preview. Pay when you want the file.`
**Primary CTA:** `Get an API key` → `/api-keys`
**Secondary:** `Read the tool reference` → docs link (builder: confirm destination
before shipping; if no public docs page exists yet, drop this button rather than
link to a 404).

**No logo bar, no testimonials, no counts of customers.** None exist. If the layout
feels like it needs a proof band, use a single line of `--ink-500` text:
`Built for the way agents actually work — start-then-poll, structured errors, and no step an agent can't retry.`

---

## 5. The connect block — exact page content

Render this verbatim (substituting the real host once deployed).

**Step 1 — Get an API key**

> Sign in and create a key at **redirx.dev/api-keys**. It's shown once, starts with
> `rdx_`, and only a hash is stored — copy it when you create it.

**Step 2 — Add the server to your client**

```bash
claude mcp add --transport http redirx https://mcp.redirx.dev/mcp \
  --header "Authorization: Bearer rdx_your_key_here"
```

**Caption below the block** (`--ink-500`, `0.8125rem`):

> Any MCP client that speaks Streamable HTTP works the same way — point it at the
> URL and set a static `Authorization: Bearer` header. Check your client's docs for
> how it sets fixed headers.

**Coming-soon line** (only if it reads naturally in the layout — one line, no badge):

> One-click OAuth sign-in is coming; today you connect with an API key.

### Builder notes on this block

- `https://mcp.redirx.dev/mcp` is a **placeholder**. `render.yaml` defines the
  service as `redirx-mcp-server` but `MCP_PUBLIC_URL` is `sync: false` — no host is
  committed to git. Confirm the real URL before publishing.
- The `--header` flag shape should be validated against the current Claude Code CLI
  before shipping; the mechanism (static bearer token) is what's verified, the exact
  CLI flag is not.
- **Do not** write "your client will prompt you to log in" — that's the OAuth path,
  and it cannot complete (§8.1).

---

## 6. FAQ

Keep to a narrow centered column (`max-width: 720px`), each Q as an H3, answers in
`--ink-700`. Static disclosure — no accordion animation beyond a 160ms height ease.

**Will my agent change my site without me?**
No. None of the four tools touch your site. `discover` reads public URLs,
`deep_match` scrapes pages, `preview` and `export` read results back. The redirect
file is handed to you as text — installing it is a step you take.

**Can my agent spend money on its own?**
No. When `export` requires payment it returns a checkout URL that must be opened by
a person in a browser. Settlement is human-in-the-loop Stripe Checkout; the agent
has no ability to complete it.

**Is the free matching a limited version of the real thing?**
It's the same engine, the same pipeline, on your full URL set. Nothing about match
quality varies by plan. What's limited is how many Deep Match runs a free account
can start in a rolling 24-hour window — and that returns a clear "retry later,"
not a degraded result.

**Do I need to install anything?**
No. It's a remote server over Streamable HTTP — no stdio wrapper, no npx package,
no local binary. You add a URL and a bearer token.

**What formats does the export produce?**
Apache, nginx, WordPress, Vercel, Cloudflare, Shopify, CSV, and JSON. You can also
set a minimum confidence to drop weak matches, and choose between request paths and
absolute URLs.

---

## 7. Accessibility gates the builder must not skip

- Every accent/ink pair in §2 is pre-verified — **do not introduce new colors.**
  Anything added needs a contrast check before it ships.
- The animated sequence (§4.2) must render fully revealed under
  `prefers-reduced-motion: reduce`, and its content must be in the DOM at load
  (revealed by opacity, not inserted by JS) so it is readable without scripts.
- Copy buttons need an accessible label and a visible confirmation, not just a
  color change.
- The one dark surface (the terminal block) needs its own verified contrast — mono
  text on `--ink-900` should be `#FBFAF8` or lighter.

---

## 8. Where the code is narrower than the pitch — verify before publishing

Three findings; the first is the one that can make the page *wrong*.

1. **`render.yaml` commits `MCP_AUTH_MODE=oauth`, not `dev`** (`render.yaml:128`),
   and `mcp-server/src/config.ts:57` defaults to `oauth` too. The API-key bearer
   path (`DevApiKeyAdapter`) only accepts keys when that variable is `dev`. So the
   connect instructions in §5 describe a *working mechanism* that the committed
   deploy config does not currently enable — and since nothing is deployed
   (unmerged branch), no host serves either path today. **Someone must flip that
   env var, or ship `/oauth/consent`, before the connect block is true.**
   Separately, `grep` for `oauth/consent` in `frontend/src/` returns zero hits,
   confirming the consent page genuinely does not exist.
2. **`discover` is plan-capped, so "free" is not "unlimited."**
   `DISCOVERY_MAX_URLS_FREE = 1000` vs `DISCOVERY_MAX_URLS_PAID = 5000`
   (`src/redirx/config.py:68-69`), and the tool's own output says
   `(truncated at N by your plan)`. The §4.1 copy above avoids claiming
   uncapped discovery — keep it that way.
3. **The free Deep Match ceiling is concrete: 5 runs per rolling 24 hours**, with a
   warning at 3 (`FREE_RUN_HARD_CAP=5`, `FREE_RUN_SOFT_CAP=3`,
   `FREE_RUN_WINDOW_HOURS=24`, `backend/services/entitlement_service.py:80-82`), all
   env-overridable. The copy above says "a limited number in a rolling 24-hour
   window" rather than naming 5, since the values are tunable — **if you'd rather
   name the number, confirm the deployed env vars first.**

Everything else checked out: four tools with the names and fields used above, MPP
`-32042` (`mcp-server/src/payments/mpp.ts:53`), the eight export formats, the
~20-row preview sample split half-lowest/half-highest confidence
(`backend/routes/v1_routes.py:374-440`), Streamable-HTTP-only transport, and
`check_migration_health` explicitly absent from the tool set.
