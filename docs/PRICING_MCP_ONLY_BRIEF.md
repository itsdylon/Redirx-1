# Redirx pricing — brief for the landing page

**Status: agreed direction, not yet built or live in Stripe.** Decided 2026-09-15.
Every price below is a proposal. Do not publish a checkout that takes money for
something that doesn't exist yet.

---

## What Redirx is now

A remote MCP server. The customer's AI agent (Claude Code, Claude, Cursor) calls it
while rebuilding or moving a website, and Redirx does the redirect work end to end:

1. Finds every page on the old site and the new site.
2. Matches each old page to its new home by reading the actual page content.
3. Repairs weak matches using the site's own renaming patterns.
4. Hands the agent a deploy-ready redirect file for their platform (Apache, Nginx,
   WordPress, Vercel, Cloudflare, Shopify, CSV, JSON), which the agent writes into
   their project.
5. After launch, checks every redirect against the live site.
6. Keeps watching, and tells the agent what to fix when something breaks.

There is no web app to sign up for and no dashboard. Sign-in, payment and connecting
Google Search Console are the only things that happen in a browser.

## Who it's for

People who work with AI agents: developers and founders rebuilding sites with coding
agents, and dev shops doing it for clients.

**Not** the target: SEO managers who don't use AI tools. Don't write copy aimed at
them, and don't lean on traditional SEO-tool comparisons.

## The pricing

**Free — sites up to 500 pages**
The complete job, at full quality: matching, repair, the redirect file, and one check
after deploy. Not a trial, not a sample. There are limits on how many runs an account
can start per day to prevent abuse, and a monthly page budget.

**Pay per migration — sites over 500 pages**
Redirx scans the site first and tells the person what it found before charging
anything. They see the price, approve once, pay, and then the full run happens.

| Site size (old pages) | Price |
|---|---|
| Up to 500 | Free |
| 501 – 1,500 | $49 |
| 1,501 – 5,000 | $99 |
| 5,001 – 15,000 | $199 |
| Over 15,000 | Custom quote |

Each paid migration includes 30 days of re-runs for that site, the post-deploy check,
and 30 days of monitoring.

**Subscription — for people doing this repeatedly**
- **Studio, $99/mo** — 5 migrations a month, monitoring for 5 sites.
- **Agency, $299/mo** — 20 migrations a month, monitoring for 20 sites, and the
  ability to send the payment link to a client.
Within the allowance the agent never stops to ask for payment.

**Monitoring after the included 30 days:** $29 per site per month.

## The pitch for large sites

The scan is free and produces the reason to pay. With Search Console connected:

> 3,400 pages. 1,380 can't be matched safely from their URLs alone. Those pages got
> 12,400 clicks in the last 90 days — 38% of your search traffic. Protect them: $99.

Without Search Console, the same message without the traffic figures ("1,380 pages
can't be matched safely"), plus an offer to connect Search Console and see what's at
risk. **Never invent or estimate traffic numbers.**

## Language rules

- **Never** say "Quick Match" or "Deep Match". Those names are retired and meant
  nothing to users.
- Customers never choose a matching method. Everyone gets the best one; large sites
  pay for it.
- Say "pages", not "credits" or "URLs processed".
- Say "migration" for one old site moving to one new site.
- Talk about the traffic they keep, not the algorithm. The product is protected
  search traffic; the redirect file is how it's delivered.

## Claims you may and may not make

**Safe:**
- The free tier does the whole job for sites up to 500 pages.
- Redirx reads page content to match pages, rather than guessing from URL names.
- It checks redirects against the live site after deploy and keeps monitoring.
- It works inside the customer's agent; no dashboard to learn.

**Do not claim:**
- Any accuracy percentage, match rate, or "X% of redirects correct". We have internal
  numbers but no clean test to back a public figure.
- Customer counts, logos, testimonials, or revenue. There are 15 users and no paying
  customers.
- Traffic recovery outcomes ("recover X% of lost traffic"). Never measured.
- That Search Console integration is available to everyone — Google verification is
  still pending, so it's limited today.
- Anything about processing speed or how long a migration takes.

## Call to action

Connecting the MCP server, inside the customer's own agent. Sign-in happens through
the agent's own authorisation flow, so there is no "create an account" step before
someone can try it. Pricing questions get answered by the agent during the job, so the
landing page's job is to get the server installed, not to close a sale.

## Open decisions that could change copy

1. **The 500-page free limit** is a placeholder pending a final number.
2. **Whether the free scan of a large site also returns its confident matches**, or
   only the risk summary. Current plan: risk summary only.
3. **Search Console availability**, which depends on Google's verification review.

Anything else: ask Dylon before writing it as fact.
