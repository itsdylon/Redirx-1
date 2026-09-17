# Pricing section brief — Redirx landing page

**Scope: the pricing section only.**
**Status: agreed direction, nothing is live in Stripe.** Prices display only — do not
build a working checkout. Decided 2026-09-15.

---

## Context in one paragraph

Redirx is a remote MCP server. A customer's AI agent calls it while rebuilding or
moving a website, and it handles the redirects end to end: finds every page on both
sites, matches each old page to its new home by reading page content, writes a
deploy-ready redirect file, checks the redirects against the live site after launch,
and keeps watching for breakage. There is no dashboard. The buyer is someone working
with an AI agent — a developer, a founder, or a dev shop.

## What to show

Three cards, equal weight. Monitoring is not an afterthought — it's the part
customers renew.

### 1. Free — sites up to 500 pages
The complete job at full quality: matching, repair, the redirect file, and one check
after launch. Not a trial and not a sample.
*Draft line: "Small site? The whole thing is free. No card, no trial timer."*

### 2. Per migration — sites over 500 pages
Redirx scans the site first and shows what's at risk before charging. The person sees
the price, approves once, and then the full run happens.

| Site size (old pages) | Price |
|---|---|
| Up to 500 | Free |
| 501 – 1,500 | $49 |
| 1,501 – 5,000 | $99 |
| 5,001 – 15,000 | $199 |
| Over 15,000 | Custom quote |

Each paid migration includes 30 days of re-runs for that site, the post-launch check,
and 30 days of monitoring.
*Draft line: "You see the price and what's at risk before anything runs."*

### 3. Monitoring — $29 per site per month
After the included 30 days. Checks every redirect on the live site on a schedule,
alerts when one breaks, and hands the fix to the customer's agent.
*Draft line: "A redirect file is a prediction. Monitoring is what checks it came true."*

### Smaller row beneath: doing this repeatedly
- **Studio — $99/mo:** 5 migrations a month, monitoring for 5 sites.
- **Agency — $299/mo:** 20 migrations a month, monitoring for 20 sites, and payment
  links you can send to a client.
One line explaining the real benefit: inside the allowance the agent never stops to
ask anyone for payment.

## Call to action

The button connects the MCP server, it does not open a checkout. Sign-in happens
through the agent's own authorisation flow, so there's no account to create first.
People pay later, in the middle of a job, from a link their agent gives them. No
"contact sales" anywhere except the over-15,000-pages row.

## Language rules

- **Never** write "Quick Match" or "Deep Match". Those names are retired.
- Customers never choose a matching method. Everyone gets the best one; large sites
  pay for it. Don't frame the paid tiers as better matching — they're bigger sites.
- Say "pages", never "credits" or "URLs processed".
- "Migration" = one old site moving to one new site.
- Sell the traffic they keep, not the algorithm. The redirect file is the delivery
  mechanism, not the product.

## Claims you may and may not make

**Safe:**
- The free tier does the whole job for sites up to 500 pages.
- Redirx reads page content to match pages, rather than guessing from URL names.
- It checks redirects against the live site after launch and keeps monitoring.
- It works inside the customer's own agent; there's no dashboard to learn.

**Do not claim:**
- Any accuracy or match-rate percentage. We have internal numbers, no clean test.
- Customer counts, logos, testimonials, or revenue. 15 users, no paying customers.
- Traffic recovery outcomes ("recover X% of lost traffic"). Never measured.
- Speed or duration of a migration.
- That Search Console traffic data is available to everyone — Google verification is
  still pending, so it's limited today. If the section mentions seeing traffic at
  risk, keep it conditional.
- Any "used by N agents / N developers" framing.

## Open items that could change these numbers

1. **The 500-page free limit is a placeholder.** It appears in three places above.
2. **Studio and Agency plan contents** are proposals; the $99/$299 prices are firmer
   than the allowances.
3. Prices are not in Stripe yet, so nothing on the page can transact.

Ask Dylon before writing anything as fact that isn't in this file.
