# Workstream 4: Reddit Outreach Automation

> **Part of [Redirx Growth Workstreams](../WORKSTREAMS.md)** — see unified doc for execution order, measurement framework, and ICP definition.

**Status:** f5bot/redreach running but unoptimized; need Telegram delivery pipeline
**Goal:** Get context-aware reply suggestions delivered to Telegram for quick review and posting

## PostHog Alignment Check

> **STRONGLY ALIGNED: Community engagement as growth driver.**
> PostHog's first 1,000 users came almost entirely from word-of-mouth. Their principle: "startups win on speed — be glued to your messages" and "respond within 30 seconds if someone messages." The Reddit pipeline is exactly this — getting you in front of relevant conversations fast so you can be genuinely helpful.

> **CRITICAL PostHog PRINCIPLE: Authenticity over marketing.**
> PostHog's "Weirdness" value: "We have a weird, unusual style because that's how we'll win." Your Reddit replies need to be genuinely YOU — helpful, opinionated, sharing real experience. The AI should draft suggestions but the voice must be yours. PostHog would never auto-post; they'd have the actual builder show up and be real.

> **ALIGNED: The human-in-the-loop design.**
> The Telegram pipeline with "review → edit → post manually" is exactly right. It's not automation that removes you from the conversation — it's automation that gets you INTO conversations faster. PostHog's approach to community was always personal, never automated.

> **CONFLICT FLAG: Be careful about "shill detection" framing.**
> PostHog's sales philosophy: "Don't make a sale if your product is not a good fit for the customer. Sometimes that means sending customers to competitors." Your Reddit system should genuinely adopt this — if someone's problem is better solved by a different tool, say so. This builds trust faster than any amount of tactful self-promotion. The engagement score system should include a "recommend competitor" option when appropriate.

## Research Findings

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

## The Reply Generation Prompt Problem

This is the hard part. The AI needs to understand Reddit culture per-subreddit. Your system prompt for the reply generator should include:

- The subreddit's self-promo rules (scrape from sidebar or hardcode for your target subs)
- Tone calibration (helpful first, product mention only if naturally relevant)
- A "shill detector" — if the thread doesn't naturally invite your product, suggest a helpful reply WITHOUT mentioning Redirx
- Reply format: genuine help first (2-3 sentences), then *optionally* "I built [tool] that does this" only if contextually appropriate

## Agent Prompt: n8n Reddit-to-Telegram Pipeline

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

## Optimizing f5bot/redreach

Since you said these are unoptimized, here are the key tuning levers:

- **Keyword refinement:** Track specific long-tail phrases, not just "redirect" (too noisy). Try: "redirect mapping", "301 redirect tool", "website migration redirects", "redirect generator", "broken links migration"
- **Subreddit targeting:** Focus on r/webdev, r/SEO, r/bigseo, r/Wordpress, r/webdesign, r/digital_marketing, r/sysadmin
- **Negative filters:** Exclude threads about network redirects, DNS redirects, gaming redirects
