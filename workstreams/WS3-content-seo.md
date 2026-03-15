# Workstream 3: Content & SEO Strategy

> **Part of [Redirx Growth Workstreams](../WORKSTREAMS.md)** — see unified doc for execution order, measurement framework, and ICP definition.

**Status:** Needs tooling setup + content calendar
**Goal:** Drive organic inbound through blog posts targeting migration/redirect keywords

## PostHog Alignment Check

> **STRONGLY ALIGNED: Content that serves the exact audience.**
> PostHog's SEO handbook states: "PostHog's content serves the exact audience who needs the product, building trust before the first transaction." They targeted 5,000 weekly organic users and hit ~4,700 consistently. Their approach: every piece of content anticipates what the reader will do next and gives them a natural path to the product.

> **CONFLICT IDENTIFIED: AI-generated content vs. authenticity.**
> PostHog's "Weirdness" value emphasizes authenticity: "We have a weird, unusual style because that's how we'll win." ALwrity and AI content mills produce generic, optimized content that sounds like everyone else. PostHog's blog is distinctly voice-y, opinionated, and shares real data/decisions. This is what made their content work.
>
> **Resolution:** Use AI tooling for research, keyword analysis, outlines, and first drafts. But the final voice should be yours — sharing real Redirx data (like "we got our first organic user and here's what happened"), real decisions, real trade-offs. This is the PostHog "build in public" approach and it creates content that can't be replicated by competitors.

> **ALIGNED: Write the tool first, then the blog post.**
> PostHog's product criteria: ship the product, then create content around it. Not the other way around. The content is downstream of the product.

## Sub-problem A: What to Write

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

## Sub-problem B: SEO Tooling Stack

**Recommended self-hosted stack (all open-source, ~$200/year total):**

1. **SerpBear** (rank tracking) — Open-source, Docker-deployable, unlimited keywords. Tracks your position for target keywords over time. This is your single source of truth for "is my SEO working?" GitHub: `towfiqi/serpbear`

2. **LibreCrawl** (technical SEO audits) — Open-source alternative to Screaming Frog. Crawl your own site and competitors. No URL limits. GitHub: `PhialsBasement/LibreCrawl`

3. **ContentSwift** (content gap analysis) — SERP-driven content optimization, alternative to Surfer/Frase. GitHub: `hilmanski/contentswift`

4. **ALwrity** (AI research & outlines only) — Use for keyword research, competitor analysis, and outline generation. Do NOT use for final content — write that yourself with your own voice and real data. PostHog's content works because it's authentically theirs. Yours should be too. GitHub: `AJaySi/ALwrity`

**Outrank.so verdict:** $99/month is steep for your stage, AND it conflicts with PostHog's authenticity principle. It produces generic optimized content. Your competitive advantage in content is your real story, real data, and real opinions. No AI content mill can replicate that.

**You already have PostHog for analytics.** Use it as your content analytics too — track which blog posts drive tool usage, not just pageviews. PostHog's principle: focus on actionable data, not vanity metrics. Pageviews are vanity; "blog reader → tool user" conversion is actionable.

## Agent Prompt: SEO Tooling Setup

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

## Agent Prompt: Content Calendar Generator

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
