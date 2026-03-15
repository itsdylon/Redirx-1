# Redirx Growth Workstreams

*Generated 2026-03-15 | Dylon's strategic planning doc*

---

## Overview

Four parallel workstreams to drive Redirx from "one organic user" to repeatable inbound growth. Each section includes context, research findings, next actions, and ready-to-use agent prompts.

Every decision in this doc is grounded in PostHog's product-led growth philosophy. Where a decision conflicts with their principles, the conflict is called out and the plan is adjusted. Key PostHog principles referenced throughout:

- **Product-led growth over sales-led growth.** The product IS the growth engine. Everything else is secondary. ([PostHog Handbook: Product-led Sales](https://posthog.com/handbook/growth/sales/product-led-sales))
- **Your ICP defines everything.** Your ideal customer profile determines not just who you target, but what you build and how you go to market. ([PostHog Handbook: Who we are building for](https://posthog.com/handbook/who-we-are-building-for))
- **Generous free tier = word-of-mouth flywheel.** Pre-PMF users who get real value for free become advocates when they grow. 97% of PostHog's early growth was developer-to-developer word-of-mouth. ([PostHog Founders: Pricing as Product](https://posthog.com/founders/how-to-treat-your-pricing-like-a-product))
- **Pricing signals seriousness.** When PostHog started charging for session replay, usage actually *increased* — people took it more seriously. Charging isn't anti-user; it's a signal that the product matters. ([PostHog Newsletter: SaaS Pricing Lessons](https://posthog.com/newsletter/saas-pricing-lessons))
- **Ship fast with small teams, then iterate.** No approval chains, no waiting. Build → ship → measure → talk to users → iterate. ([PostHog Founders: How we ship so much](https://posthog.com/founders/how-come-we-ship-so-much))
- **Content serves the exact audience who needs the product.** Trust before transaction. ([PostHog Handbook: SEO Guide](https://posthog.com/handbook/growth/marketing/seo-guide))
- **Transparency and authenticity drive word-of-mouth.** Build in public, share your journey, be weird. ([PostHog Handbook: Values](https://posthog.com/handbook/values))

---

## Workstream 1: Conversion Funnel Optimization

**Status:** Highest priority — you have traffic, you need conversion
**Goal:** Turn quick match users into deep match paying customers

### PostHog Alignment Check

> **CONFLICT IDENTIFIED: "X of Y" gating vs. generous free tier.**
> PostHog's core freemium principle is that the free tier should deliver *real, complete value* so users become advocates. Gating quick match results behind "show 5 of 42" creates an artificial restriction on the FREE tool — the thing that's supposed to generate word-of-mouth. If users feel the free tool is crippled, they won't recommend it.
>
> **Resolution:** Quick match should show ALL results, ungated. The conversion mechanism should come from showing what quick match *can't* do, not from hiding what it *did* do. This aligns with PostHog's session replay insight: when they started charging for it, they didn't cripple the free product — they made the paid tier genuinely better, and usage of both tiers increased.

> **ALIGNED: Confidence scores and contextual upsell.**
> PostHog found that "people take your product more seriously if it's obvious you're taking it seriously." Showing confidence scores (70% quick vs 94% deep) is a technical, honest comparison — not an artificial gate. This reframes deep match as a genuinely better product, not a paywall. This is the PostHog way: let the product sell itself by being visibly better.

> **ALIGNED: Pricing signals seriousness.**
> Your one organic user used the tool twice in one day — he got real value from the free tier. That's the flywheel working. Now you need to show that the paid tier is worth paying for, not make the free tier worse.

### Key Research Findings (PostHog-Adjusted)

The current flow has two friction points: (1) no active upsell mechanism during/after quick match, and (2) opt-in consent required for deep match scanning. Both are solvable, but the approach must preserve the free tier's completeness.

**Quick wins ranked by ROI (adjusted for PostHog alignment):**

1. **Confidence score display** (~2 days) — Show *"Quick Match: ~70% confidence (pattern-based)"* alongside each result. At the bottom: *"Deep Match achieves ~99% confidence using content analysis. [Try it →]"*. This is honest, technical, and lets the product speak for itself. No gating, no artificial restriction.
   - *PostHog principle: The product IS the growth engine. Show don't gatekeep.*

2. **Side-by-side comparison after results** (~1-2 days) — After showing ALL quick match results, display a comparison panel: what quick match did vs what deep match would do differently. If you can cheaply run deep match on 2-3 sample URLs and show the difference, this is the highest-converting pattern because it's *proof*, not a promise.
   - *PostHog principle: "Don't make a sale if your product is not a good fit." Show the difference; let them decide.*

3. **Loading screen value messaging** (~1 day) — Two distinct loading experiences:
   - **Quick match loading:** Rotate educational messages about what deep match does differently: *"Deep Match analyzes actual page content, not just URL patterns"* and *"Content-aware matching catches pages that moved to completely different URLs."* Frame as education, not sales.
   - **Deep match loading (post-conversion):** Deep match takes significantly longer due to content scraping. Use playful, honest messaging: *"Please be patient — ~~greatness~~ content scraping takes time (estimate: X minutes)"*. Show real-time progress (pages scraped, embeddings generated). The humor + transparency keeps users engaged during a longer wait and reinforces that something substantial is happening — this isn't a fake loading bar.
   - *PostHog principle: Content serves the exact audience who needs the product, building trust before the transaction. AND: Transparency builds trust — be honest about the wait time, make it fun.*

4. **Contextual permission request** (~1 day) — Move the deep match opt-in to *after* showing quick match results. Pre-explain what you'll scan and why. Add trust signals: data lifecycle transparency ("scanned data deleted after matching"), robots.txt respect, processing speed estimate. **Critical disclaimer: users must disable spam protection (e.g., Wordfence, Cloudflare bot protection) or whitelist Redirx's server IP before deep match can crawl their site — otherwise their firewall will block the scraper and potentially blacklist the server IP.** This needs to be prominent in the consent flow, not buried in fine print.
   - *PostHog principle: Transparency builds trust. Tell them exactly what happens, be honest, be open. The Wordfence warning is a perfect example — being upfront about this prevents a bad user experience AND protects your infrastructure.*

5. **Progressive consent flow** (~2-3 days) — Step 1: Show ALL quick results (complete value). Step 2: "Want higher accuracy? We'll scan your public pages" with one-click consent. Step 3: Real-time progress during scan. Step 4: Improved results with visible confidence boost. Contextual approach beats upfront permission by 40%+.
   - *PostHog principle: "Why not now?" — remove friction, ask at the right moment, ship it.*

### Agent Prompt: Conversion Funnel Implementation Planner

```
You are helping me plan the implementation of conversion funnel improvements for Redirx,
a website migration redirect mapping tool built with Flask + React (Vite).

Context:
- Free tier: "Quick Match" (URL pattern matching, no scraping needed)
- Paid tier: "Deep Match" (content-aware matching using embeddings, requires site scraping)
- The conversion funnel is not active yet — quick match users see results but have no upsell path
- Deep match requires explicit user consent to scrape their site (adds friction)

The codebase is in this repo. Frontend is in /frontend (React + Vite), backend is Flask.
Read the CLAUDE.md for full architecture details.

I want to implement these changes in priority order:
1. Add confidence scores to quick match results (e.g., "~70% confidence") with a comparison
   showing deep match achieves ~99%. Show ALL quick match results ungated — the free tier
   should deliver complete value (PostHog principle: generous free tier drives word-of-mouth).
2. Add a side-by-side comparison panel after results showing what deep match would do differently
3. Add rotating educational tip messages to the quick match loading screen
4. Add a deep match loading screen with playful copy: "Please be patient — ~~greatness~~
   content scraping takes time (estimate: X minutes)" with real-time progress (pages scraped,
   embeddings generated). Deep match is slow due to content scraping — own it with humor.
5. Build a contextual deep match opt-in flow that appears after quick match results
6. Add trust signals to the consent modal including:
   - Data lifecycle transparency ("scanned data deleted after matching")
   - robots.txt respect notice
   - **CRITICAL: Prominent warning that users must disable spam protection (e.g., Wordfence,
     Cloudflare bot protection) or whitelist Redirx's server IP before running deep match.
     Otherwise their firewall will block the scraper and potentially blacklist our server IP.**
   - This should NOT be fine print — make it a clear, styled callout in the consent flow

For each change:
- Identify the exact files that need modification
- Propose the UI/UX approach
- Write the implementation code
- Ensure it works with the existing pipeline architecture

Start by reading CLAUDE.md and exploring the frontend components to understand the current flow.
```

---

## Workstream 2: Free Tool Expansion & Prioritization

**Status:** Needs strategic decision before building
**Goal:** Expand the free tool surface area to capture more organic traffic and funnel to deep match

### PostHog Alignment Check

> **ALIGNED: Building free tools that solve real problems.**
> PostHog's entire growth model is built on this: the free product delivers complete value, users love it, they tell others. Each free tool you build is a new word-of-mouth vector. PostHog's handbook on product decisions says shipping order matters — "shipping them in the right order is key to a fast return on investment from every new product."

> **IMPORTANT PostHog PRINCIPLE: "Does it solve problems that don't change as company gets bigger?"**
> This is from PostHog's product criteria. The crawl-first mapper passes this test — people will always need to discover URLs before mapping them. The broken link checker also passes — broken links are an eternal problem. These aren't trend-dependent.

> **ALIGNED: Talk to users to validate priority.**
> You have one real organic user. PostHog would say: talk to him. Ask what his experience was like, what was hard, what he wished was different. PostHog found that "talking to users is a short-term investment in long-term productivity." Your PostHog session replay data IS a form of this — you're watching what he actually did, not what he said. That's even better. But also reach out directly. You already did outreach, so build on that relationship.

> **ADJUSTED: Use the "ship order" framework, not just keyword volume.**
> The original priority matrix weighted "organic potential" too heavily. PostHog's framework asks: (1) Does it solve a problem for your ICP? (2) Is it easy to integrate with what exists? (3) Can you ship it fast? Applying this shifts the priority slightly.

### Priority Assessment (PostHog-Adjusted)

| Tool Idea | Solves ICP Problem? | Integrates Easily? | Ship Speed | Funnel to Paid | Priority |
|-----------|-------------------|-------------------|-----------|----------------|----------|
| **Crawl-first URL mapper** | Yes — removes sitemap friction (you observed this in PostHog) | Yes — builds on existing pipeline | Fast (2-3 days) | Direct (same funnel) | **#1** |
| **Broken link checker** | Somewhat — adjacent problem, not core ICP need | New code but reuses aiohttp patterns | Medium (2-3 days) | Indirect | **#2 but validate first** |
| **Redirect chain detector** | Yes — directly relevant to migration users | Very easy — lightweight HTTP check | Very fast (1 day) | Direct | **#2 (tie)** |

**Key adjustment:** Before building the broken link checker, validate that your ICP (people doing website migrations) actually wants this tool from *you*. Broken link checking is a crowded space with established free tools (Ahrefs free checker, Dead Link Checker, etc.). PostHog's product criteria asks "are there major competitors already in market?" — the answer here is yes. The redirect chain detector is less crowded and more directly relevant to your ICP. Consider shipping it first since it's only a day of work, then measure whether it drives the right traffic.

### The Crawl-First Mapper (Priority #1)

This solves a real friction point you observed in PostHog session data: non-technical users don't have sitemaps ready. The flow would be:

1. User enters old site URL + new site URL (just the domains)
2. Tool auto-discovers pages via sitemap.xml, or crawls if no sitemap
3. Runs quick match on discovered pages
4. Shows results with confidence comparison to deep match

This unlocks a new content angle ("How to map redirects with just your URLs") and removes the sitemap friction entirely. The crawling component already exists in your pipeline (WebScraperStage) — you'd just need a lightweight sitemap discovery + crawl layer on top.

*PostHog principle: "Your product is downstream from your ideal customer profile." This feature directly removes friction your ICP experiences. Ship it.*

### The Broken Link Checker (Priority #2, Needs Validation)

This captures a massive keyword space but has strong existing competition. Before building, ask: will the people who use a broken link checker actually convert to redirect mapping? The funnel is indirect. PostHog would say: don't build it on assumption — find signal first. Write the blog post about broken links and redirects, include a CTA to the existing quick match tool, and see if that content drives the right traffic. If it does, then build the dedicated tool.

*PostHog principle: "Until products are built and launched, it's hard to predict which will do well." But you can de-risk with content-first validation.*

### Agent Prompt: Crawl-First URL Mapper

```
I want to add a "crawl-first" URL mapper to Redirx. Currently, users must provide
sitemaps or URL lists. The new flow should be:

1. User enters just two domains: old site URL and new site URL
2. System auto-discovers pages via:
   a. Check for sitemap.xml at standard locations (/sitemap.xml, /sitemap_index.xml)
   b. If no sitemap found, do a lightweight crawl (follow internal links, cap at 200 pages)
3. Run the existing quick match pipeline on discovered URLs
4. Show results with the conversion funnel (X of Y gating, confidence scores, etc.)

Technical context:
- Read CLAUDE.md for the full pipeline architecture
- WebScraperStage already handles async scraping with aiohttp
- The pipeline accepts tuple[list[str], list[str]] as input
- I need a new SitemapDiscoveryStage or similar that runs BEFORE the existing pipeline
- Must respect robots.txt
- Should show real-time progress as pages are discovered

Deliverables:
1. A sitemap discovery module (check sitemap.xml, parse it, extract URLs)
2. A lightweight crawler fallback (BFS crawl following internal links, respecting robots.txt)
3. Integration with the existing pipeline
4. Frontend UI: two input fields (old domain, new domain) + progress indicator
5. Rate limiting to be respectful (1 req/sec default)

Start by reading CLAUDE.md and the existing stages.py to understand the pipeline patterns.
```

### Agent Prompt: Broken Link Checker Tool

```
I want to add a free "Broken Link Checker" tool to Redirx as a separate tool page.
The tool should:

1. Accept a single URL (user's website)
2. Crawl the site and check all internal + external links for broken responses (404, 5xx, timeouts)
3. Report broken links with: source page, broken URL, status code, anchor text
4. Upsell: "Found 23 broken links. Generate redirects automatically with Deep Match."

Technical requirements:
- Async crawling using aiohttp (consistent with existing codebase patterns)
- Respect robots.txt
- Cap at 500 pages for free tier
- Show real-time progress
- Results exportable as CSV
- New Flask route + React page

Read CLAUDE.md for architecture context. The tool should be a standalone page
but share the existing backend infrastructure (Flask app, async patterns).
Build it as a separate blueprint in the backend.
```

---

## Workstream 3: Content & SEO Strategy

**Status:** Needs tooling setup + content calendar
**Goal:** Drive organic inbound through blog posts targeting migration/redirect keywords

### PostHog Alignment Check

> **STRONGLY ALIGNED: Content that serves the exact audience.**
> PostHog's SEO handbook states: "PostHog's content serves the exact audience who needs the product, building trust before the first transaction." They targeted 5,000 weekly organic users and hit ~4,700 consistently. Their approach: every piece of content anticipates what the reader will do next and gives them a natural path to the product.

> **CONFLICT IDENTIFIED: AI-generated content vs. authenticity.**
> PostHog's "Weirdness" value emphasizes authenticity: "We have a weird, unusual style because that's how we'll win." ALwrity and AI content mills produce generic, optimized content that sounds like everyone else. PostHog's blog is distinctly voice-y, opinionated, and shares real data/decisions. This is what made their content work.
>
> **Resolution:** Use AI tooling for research, keyword analysis, outlines, and first drafts. But the final voice should be yours — sharing real Redirx data (like "we got our first organic user and here's what happened"), real decisions, real trade-offs. This is the PostHog "build in public" approach and it creates content that can't be replicated by competitors.

> **ALIGNED: Write the tool first, then the blog post.**
> PostHog's product criteria: ship the product, then create content around it. Not the other way around. The content is downstream of the product.

### Sub-problem A: What to Write

Content should be directly tied to your tools AND include your real data/story. Each free tool unlocks a content angle:

| Blog Post Idea | Target Keywords | Tied to Tool | PostHog "Build in Public" Angle |
|---------------|----------------|-------------|-------------------------------|
| "How to Map Redirects with Just Your URLs" | redirect mapping, URL mapper | Crawl-first mapper | Share how you built the crawler, what you learned |
| "I Built a Free Redirect Mapping Tool — Here's What I Learned" | redirect mapping tool | Quick match | Your pivot story, first user, PostHog data |
| "301 Redirect Best Practices for Website Migrations" | 301 redirect guide | Deep match | Real examples from Redirx data |
| "How to Audit Your Site's Redirect Chains" | redirect chain checker | Redirect chain detector | Technical walkthrough with real code |
| "How to Find and Fix Broken Links During a Migration" | broken links migration | Content validation | Practical guide, test broken link checker demand |

*PostHog principle: "Build in public, share your journey." Your story of getting the first organic user, pivoting to freemium, watching the session in PostHog — that IS content. It's authentic, it's unique, and it naturally showcases both Redirx and the builder ethos that resonates with developers.*

**Priority order for first 3 posts:**

1. **"I Built a Free Redirect Tool and Got My First Organic User"** — Build-in-public post. Share the pivot, the PostHog session, what you learned. Targets indie hacker / developer audience. This builds your personal brand AND generates backlinks from communities like HN, r/SaaS, IndieHackers. No tool dependency — write it now.

2. **"How to Map Redirects with Just Your URLs"** — Ship crawl-first mapper first, then write this. Practical tutorial that naturally embeds the tool.

3. **"301 Redirect Best Practices for Website Migrations"** — Evergreen SEO content targeting high-volume keywords. Include real examples.

### Sub-problem B: SEO Tooling Stack

**Recommended self-hosted stack (all open-source, ~$200/year total):**

1. **SerpBear** (rank tracking) — Open-source, Docker-deployable, unlimited keywords. Tracks your position for target keywords over time. This is your single source of truth for "is my SEO working?" GitHub: `towfiqi/serpbear`

2. **LibreCrawl** (technical SEO audits) — Open-source alternative to Screaming Frog. Crawl your own site and competitors. No URL limits. GitHub: `PhialsBasement/LibreCrawl`

3. **ContentSwift** (content gap analysis) — SERP-driven content optimization, alternative to Surfer/Frase. GitHub: `hilmanski/contentswift`

4. **ALwrity** (AI research & outlines only) — Use for keyword research, competitor analysis, and outline generation. Do NOT use for final content — write that yourself with your own voice and real data. PostHog's content works because it's authentically theirs. Yours should be too. GitHub: `AJaySi/ALwrity`

**Outrank.so verdict:** $99/month is steep for your stage, AND it conflicts with PostHog's authenticity principle. It produces generic optimized content. Your competitive advantage in content is your real story, real data, and real opinions. No AI content mill can replicate that.

**You already have PostHog for analytics.** Use it as your content analytics too — track which blog posts drive tool usage, not just pageviews. PostHog's principle: focus on actionable data, not vanity metrics. Pageviews are vanity; "blog reader → tool user" conversion is actionable.

### Agent Prompt: SEO Tooling Setup

```
Help me set up a self-hosted SEO tooling stack on my VPS using Docker Compose.
I need to deploy:

1. SerpBear (rank tracking) - https://github.com/towfiqi/serpbear
2. ALwrity (AI content generation) - https://github.com/AJaySi/ALwrity

For SerpBear:
- Configure it to track these seed keywords: "redirect mapping tool", "301 redirect generator",
  "website migration redirects", "free redirect mapper", "broken link checker"
- Set up weekly email reports
- Connect Google Search Console if possible

For ALwrity:
- Configure with Claude API (I have an Anthropic API key)
- Set up for blog post generation targeting the keyword list above

Create a docker-compose.yml that runs both services behind a reverse proxy (Caddy or nginx).
Include instructions for DNS setup and SSL.
```

### Agent Prompt: Content Calendar Generator

```
I need a 3-month content calendar for the Redirx blog targeting website migration
and redirect-related keywords.

Context:
- Redirx is a free redirect mapping tool with a paid "deep match" tier
- Target audience: web developers, SEO specialists, marketing teams doing site migrations
- I'm planning to add these free tools: crawl-first URL mapper, broken link checker
- Each blog post should naturally tie back to one of these tools

For each post, provide:
- Title (SEO-optimized, targeting specific keywords)
- Target keyword cluster
- Brief outline (3-5 sections)
- Which Redirx tool it ties to
- Estimated keyword difficulty and search volume (use your knowledge of SEO metrics)
- Suggested publish date (weekly cadence)

Prioritize posts that target high-intent, lower-competition keywords first.
Format as a table I can import into a project management tool.
```

---

## Workstream 4: Reddit Outreach Automation

**Status:** f5bot/redreach running but unoptimized; need Telegram delivery pipeline
**Goal:** Get context-aware reply suggestions delivered to Telegram for quick review and posting

### PostHog Alignment Check

> **STRONGLY ALIGNED: Community engagement as growth driver.**
> PostHog's first 1,000 users came almost entirely from word-of-mouth. Their principle: "startups win on speed — be glued to your messages" and "respond within 30 seconds if someone messages." The Reddit pipeline is exactly this — getting you in front of relevant conversations fast so you can be genuinely helpful.

> **CRITICAL PostHog PRINCIPLE: Authenticity over marketing.**
> PostHog's "Weirdness" value: "We have a weird, unusual style because that's how we'll win." Your Reddit replies need to be genuinely YOU — helpful, opinionated, sharing real experience. The AI should draft suggestions but the voice must be yours. PostHog would never auto-post; they'd have the actual builder show up and be real.

> **ALIGNED: The human-in-the-loop design.**
> The Telegram pipeline with "review → edit → post manually" is exactly right. It's not automation that removes you from the conversation — it's automation that gets you INTO conversations faster. PostHog's approach to community was always personal, never automated.

> **CONFLICT FLAG: Be careful about "shill detection" framing.**
> PostHog's sales philosophy: "Don't make a sale if your product is not a good fit for the customer. Sometimes that means sending customers to competitors." Your Reddit system should genuinely adopt this — if someone's problem is better solved by a different tool, say so. This builds trust faster than any amount of tactful self-promotion. The engagement score system should include a "recommend competitor" option when appropriate.

### Research Findings

**No existing tool does the full pipeline** (monitoring → AI reply generation → Telegram delivery). Every commercial tool (ReplyAgent, Reppit AI, ReplierAI) stops at either a dashboard or auto-posting. None support Telegram as an output channel.

**Best path: DIY with n8n** (~$30/month, 2-4 hours setup)

The recommended architecture:

```
f5bot/redreach (you already have this)
       ↓ email alerts
n8n (self-hosted, free)
       ↓ parses alert, fetches Reddit thread context
Claude/GPT API
       ↓ generates context-aware reply draft
Telegram Bot API
       ↓ sends you: context summary, suggested reply, direct link to thread
You review → edit → post manually
```

**Existing n8n templates to start from:**
- `n8n.io/workflows/10246` — Reddit → AI → Telegram digest
- `n8n.io/workflows/8120` — Reddit monitoring + GPT analysis + Telegram alerts

**Open-source alternative: RedoraAI** (GitHub: `donebyai-team/RedoraAI`) — Full lead gen pipeline (monitor → generate → post) that you could fork and add Telegram to. More engineering effort but more control.

### The Reply Generation Prompt Problem

This is the hard part. The AI needs to understand Reddit culture per-subreddit. Your system prompt for the reply generator should include:

- The subreddit's self-promo rules (scrape from sidebar or hardcode for your target subs)
- Tone calibration (helpful first, product mention only if naturally relevant)
- A "shill detector" — if the thread doesn't naturally invite your product, suggest a helpful reply WITHOUT mentioning Redirx
- Reply format: genuine help first (2-3 sentences), then *optionally* "I built [tool] that does this" only if contextually appropriate

### Agent Prompt: n8n Reddit-to-Telegram Pipeline

```
Help me build an n8n workflow (self-hosted) that does this:

1. TRIGGER: Receive email alerts from f5bot (keyword monitoring for Reddit mentions
   of: "redirect mapping", "website migration", "301 redirects", "broken links",
   "redirect generator")

2. PARSE: Extract from the email:
   - Reddit thread URL
   - Subreddit name
   - The post/comment text that triggered the alert
   - Post title

3. ENRICH: Fetch the Reddit thread context via Reddit API (or JSON endpoint):
   - Original post content
   - Top 3-5 existing comments
   - Subreddit rules (specifically self-promotion rules)

4. GENERATE: Call Claude API with this prompt template:
   """
   You are helping me engage authentically on Reddit about website migration tools.

   Subreddit: {subreddit}
   Subreddit self-promo rules: {rules}
   Thread title: {title}
   Thread content: {content}
   Existing replies: {top_comments}

   Generate a helpful reply that:
   - Addresses the poster's actual problem first (2-3 sentences of genuine help)
   - Only mentions Redirx (a free redirect mapping tool) IF the thread is specifically
     about redirect mapping or migration tooling AND the subreddit allows tool mentions
   - If mentioning Redirx, frame it as "I built a free tool that does this" not a sales pitch
   - If a competitor tool is genuinely better for their specific problem, recommend that instead.
     Building trust by being honest > getting one click. (PostHog: "Don't make a sale if your
     product is not a good fit for the customer.")
   - If the thread isn't a natural fit, just be helpful with no product mention
   - Match the tone of the subreddit (technical for r/webdev, casual for r/SEO, etc.)
   - Never use marketing language, superlatives, or emojis
   - Sound like a real person sharing experience, not a brand account

   Also provide:
   - CONTEXT: 1-sentence summary of why this alert is relevant
   - ENGAGEMENT_SCORE: 1-10 how good an opportunity this is to reply
   - REPLY_TYPE: "helpful_only" | "helpful_with_mention" | "recommend_competitor"
   - If REPLY_TYPE is "recommend_competitor", explain why and which tool is better for this case
   """

5. DELIVER via Telegram Bot:
   Format:
   ---
   📍 r/{subreddit} | Score: {engagement_score}/10 | {reply_type}

   **Thread:** {title}
   **Context:** {context_summary}

   **Suggested Reply:**
   {generated_reply}

   [Open Thread]({reddit_url})
   ---

6. Include a Telegram inline keyboard with:
   - "Open Thread" button (link to Reddit)
   - "Skip" button (logs as skipped for future tuning)

Create the n8n workflow JSON and provide setup instructions including:
- n8n Docker deployment
- Telegram bot creation via BotFather
- Reddit API credentials setup
- Claude API integration
- f5bot email parsing configuration
```

### Optimizing f5bot/redreach

Since you said these are unoptimized, here are the key tuning levers:

- **Keyword refinement:** Track specific long-tail phrases, not just "redirect" (too noisy). Try: "redirect mapping", "301 redirect tool", "website migration redirects", "redirect generator", "broken links migration"
- **Subreddit targeting:** Focus on r/webdev, r/SEO, r/bigseo, r/Wordpress, r/webdesign, r/digital_marketing, r/sysadmin
- **Negative filters:** Exclude threads about network redirects, DNS redirects, gaming redirects

---

## Execution Order (PostHog-Adjusted)

PostHog's shipping philosophy: small scope, ship fast, measure, iterate. Don't plan a month out in detail — plan the first sprint, measure what happens, and let data drive the next one. "Why not now?"

```
Week 1:  SHIP & MEASURE
         Workstream 1 — Confidence scores + side-by-side comparison (no gating!)
         Workstream 4 — Set up n8n + Telegram bot (parallel, different skill set)
         Workstream 3 — Write "I Built a Free Redirect Tool" build-in-public post (no tool dependency)
         ALSO: Reach out to your organic user. Ask 3 questions. (PostHog: "talk to users")

Week 2:  SHIP & MEASURE
         Workstream 2 — Build crawl-first URL mapper
         Workstream 1 — Loading screen value messaging + consent flow
         Workstream 3 — Deploy SerpBear, start tracking keywords
         MEASURE: Check PostHog for Week 1 conversion data. Did confidence scores change behavior?

Week 3:  ITERATE BASED ON DATA
         Workstream 3 — Write "How to Map Redirects with Just Your URLs" (tool is live now)
         Workstream 2 — Ship redirect chain detector (1 day, validates demand)
         Workstream 4 — Tune reply prompts based on first week of Telegram data
         MEASURE: Which free tool drives more deep match interest? Quick match or crawl mapper?

Week 4:  DATA-DRIVEN DECISIONS
         Review all PostHog data from weeks 1-3
         Decide: Is broken link checker worth building? (Does the blog post drive traffic?)
         Workstream 3 — Write next blog post based on what's actually working
         Workstream 1 — Iterate on conversion flow based on real user behavior
```

The key PostHog difference: every week has a MEASURE step. You already have PostHog instrumented — use it. Don't build for a month and then check if it worked. Check weekly and adjust.

---

## Measurement Framework (PostHog-Aligned)

PostHog's handbook on metrics: "Focus on actionable data, not vanity metrics. Looking at the same metrics regularly increases understanding of how they relate to each other."

**Weekly dashboard (set up in PostHog):**

| Metric | What It Tells You | Vanity or Actionable? |
|--------|-------------------|----------------------|
| Quick match completions | Is the free tool being used? | Actionable |
| Quick match → deep match click-through | Is the conversion funnel working? | **Most actionable — your #1 metric** |
| Deep match opt-in consent rate | Is the trust/permission flow working? | Actionable |
| Deep match → payment conversion | Is the product worth paying for? | Actionable |
| Blog → tool usage | Is content driving the right traffic? | Actionable |
| Total pageviews | Feel-good number | Vanity (track but don't optimize for) |
| Reddit replies posted / week | Are you showing up in conversations? | Actionable |

**The one metric that matters most right now:** Quick match → deep match click-through rate. Everything else is secondary. PostHog calls this your "north star" — the single number that tells you if the flywheel is spinning.

---

## ICP Definition (Do This First)

PostHog says: "Your product is downstream from your ideal customer profile — they are who you are building for and are the most important factor for deciding what to build."

Before executing any of this, write down your ICP in one sentence. Based on your context, a starting hypothesis:

> **Redirx ICP:** Web developers or SEO specialists at small-to-mid agencies who are actively migrating a client's website and need to generate 301 redirects quickly without manual URL-by-URL matching.

Validate this against your one organic user. Does he fit? If not, adjust. Everything in this doc — which tools to build, what content to write, where to engage on Reddit — flows from this definition. If the ICP changes, the priorities change.

*PostHog principle: "An accurate ICP will define not just which customers you target, but every aspect of your product and go-to-market strategy."*
