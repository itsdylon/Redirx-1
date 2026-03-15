# Workstream 2: Free Tool Expansion & Prioritization

> **Part of [Redirx Growth Workstreams](../WORKSTREAMS.md)** — see unified doc for execution order, measurement framework, and ICP definition.

**Status:** Needs strategic decision before building
**Goal:** Expand the free tool surface area to capture more organic traffic and funnel to deep match

## PostHog Alignment Check

> **ALIGNED: Building free tools that solve real problems.**
> PostHog's entire growth model is built on this: the free product delivers complete value, users love it, they tell others. Each free tool you build is a new word-of-mouth vector. PostHog's handbook on product decisions says shipping order matters — "shipping them in the right order is key to a fast return on investment from every new product."

> **IMPORTANT PostHog PRINCIPLE: "Does it solve problems that don't change as company gets bigger?"**
> This is from PostHog's product criteria. The crawl-first mapper passes this test — people will always need to discover URLs before mapping them. The broken link checker also passes — broken links are an eternal problem. These aren't trend-dependent.

> **ALIGNED: Talk to users to validate priority.**
> You have one real organic user. PostHog would say: talk to him. Ask what his experience was like, what was hard, what he wished was different. PostHog found that "talking to users is a short-term investment in long-term productivity." Your PostHog session replay data IS a form of this — you're watching what he actually did, not what he said. That's even better. But also reach out directly. You already did outreach, so build on that relationship.

> **ADJUSTED: Use the "ship order" framework, not just keyword volume.**
> The original priority matrix weighted "organic potential" too heavily. PostHog's framework asks: (1) Does it solve a problem for your ICP? (2) Is it easy to integrate with what exists? (3) Can you ship it fast? Applying this shifts the priority slightly.

## Priority Assessment (PostHog-Adjusted)

| Tool Idea | Solves ICP Problem? | Integrates Easily? | Ship Speed | Funnel to Paid | Priority |
|-----------|-------------------|-------------------|-----------|----------------|----------|
| **Crawl-first URL mapper** | Yes — removes sitemap friction (you observed this in PostHog) | Yes — builds on existing pipeline | Fast (2-3 days) | Direct (same funnel) | **#1** |
| **Broken link checker** | Somewhat — adjacent problem, not core ICP need | New code but reuses aiohttp patterns | Medium (2-3 days) | Indirect | **#2 but validate first** |
| **Redirect chain detector** | Yes — directly relevant to migration users | Very easy — lightweight HTTP check | Very fast (1 day) | Direct | **#2 (tie)** |

**Key adjustment:** Before building the broken link checker, validate that your ICP (people doing website migrations) actually wants this tool from *you*. Broken link checking is a crowded space with established free tools (Ahrefs free checker, Dead Link Checker, etc.). PostHog's product criteria asks "are there major competitors already in market?" — the answer here is yes. The redirect chain detector is less crowded and more directly relevant to your ICP. Consider shipping it first since it's only a day of work, then measure whether it drives the right traffic.

## The Crawl-First Mapper (Priority #1)

This solves a real friction point you observed in PostHog session data: non-technical users don't have sitemaps ready. The flow would be:

1. User enters old site URL + new site URL (just the domains)
2. Tool auto-discovers pages via sitemap.xml, or crawls if no sitemap
3. Runs quick match on discovered pages
4. Shows results with confidence comparison to deep match

This unlocks a new content angle ("How to map redirects with just your URLs") and removes the sitemap friction entirely. The crawling component already exists in your pipeline (WebScraperStage) — you'd just need a lightweight sitemap discovery + crawl layer on top.

*PostHog principle: "Your product is downstream from your ideal customer profile." This feature directly removes friction your ICP experiences. Ship it.*

## The Broken Link Checker (Priority #2, Needs Validation)

This captures a massive keyword space but has strong existing competition. Before building, ask: will the people who use a broken link checker actually convert to redirect mapping? The funnel is indirect. PostHog would say: don't build it on assumption — find signal first. Write the blog post about broken links and redirects, include a CTA to the existing quick match tool, and see if that content drives the right traffic. If it does, then build the dedicated tool.

*PostHog principle: "Until products are built and launched, it's hard to predict which will do well." But you can de-risk with content-first validation.*

## Agent Prompt: Crawl-First URL Mapper

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

## Agent Prompt: Broken Link Checker Tool

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
