# Redirx Pricing Reference

> Deprecated (Pricing V1): this file describes the retired credit/trial/founder model.
> Do not use this file for current product, billing, or deployment decisions.
> Use `RedirX_Pricing_Strategy.docx`, `docs/PRICING_V2_RELEASE_RUNBOOK.md`, and `docs/PRICING_V2_DEPLOYMENT_AGENT_HANDOFF.md` instead.

---

## Unit Definition

**1 Mapping Credit = 1 page processed in a single content-based (Deep Match) mapping run.**

- Credits are consumed when the Deep Match pipeline processes a page (scraping, embedding, semantic pairing).
- If a user uploads 200 old-site URLs and 300 new-site URLs, the run costs **500 credits** (each page is processed once).

---

## Quick Match (URL-Only Pipeline)

Quick Match uses URL pattern matching (slug comparison, TF-IDF cosine similarity, RapidFuzz fallback) with **zero API cost** — no scraping, no embeddings.

| Plan | Quick Match Access |
|------|--------------------|
| Launch (Free Trial) | 2,500 matches/month |
| All paid plans | **Unlimited** |
| Founding Migration License | **Unlimited** |

---

## Subscription Tiers

### Launch (Free Trial)

| | |
|---|---|
| **Price** | Free (14-day trial) |
| **Deep Match Credits** | No (upgrade required) |
| **Quick Match** | 2,500 matches/month |
| **Projects** | 1 |
| **Export Formats** | CSV only |
| **Support** | Community |

### Starter

| | |
|---|---|
| **Price** | $49/mo \| $490/yr (save 17%) |
| **Deep Match Credits** | 50,000/month |
| **Quick Match** | Unlimited |
| **Projects** | 1 |
| **Export Formats** | All (CSV, .htaccess, Nginx, Vercel, WordPress) |
| **Overage** | $15 per additional 50,000 credits |
| **Support** | Email |

### Growth

| | |
|---|---|
| **Price** | $149/mo \| $1,490/yr (save 17%) |
| **Deep Match Credits** | 250,000/month |
| **Quick Match** | Unlimited |
| **Projects** | 3 |
| **Export Formats** | All |
| **Overage** | $40 per additional 250,000 credits |
| **Support** | Priority email |

### Scale

| | |
|---|---|
| **Price** | $399/mo \| $3,990/yr (save 17%) |
| **Deep Match Credits** | 1,000,000/month |
| **Quick Match** | Unlimited |
| **Projects** | 10 |
| **Export Formats** | All |
| **Overage** | $150 per additional 1,000,000 credits |
| **Support** | Dedicated account manager |

### Enterprise

| | |
|---|---|
| **Price** | Custom pricing |
| **Deep Match Credits** | Custom |
| **Quick Match** | Unlimited |
| **Projects** | Unlimited |
| **Export Formats** | All + custom integrations |
| **Features** | SSO/SAML, data residency, SLAs, audit logs |
| **Support** | Dedicated + SLA |

---

## Founding Migration License (One-Time Purchase)

| | |
|---|---|
| **Price** | $999 one-time (no recurring fees) |
| **Deep Match Credits** | 500,000 lifetime (never expire) |
| **Quick Match** | Unlimited |
| **Projects** | 2 |
| **Seats** | 2 |
| **Export Formats** | All |
| **Support** | Priority email |

- Credits do not renew; once used, they are gone.
- Ideal for agencies or consultants with a single large migration project.
- Limited availability (founding offer).

---

## Feature Comparison Matrix

| Feature | Launch | Starter | Growth | Scale | Enterprise | Founder |
|---------|--------|---------|--------|-------|------------|---------|
| Quick Match | 2,500/mo | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited |
| Deep Match Credits | No | 50K/mo | 250K/mo | 1M/mo | Custom | 500K lifetime |
| Projects | 1 | 1 | 3 | 10 | Unlimited | 2 |
| CSV Export | Yes | Yes | Yes | Yes | Yes | Yes |
| .htaccess / Nginx / Vercel | No | Yes | Yes | Yes | Yes | Yes |
| Alternative Suggestions | No | Yes | Yes | Yes | Yes | Yes |
| API Access | No | No | Yes | Yes | Yes | No |
| SSO / SAML | No | No | No | No | Yes | No |
| Data Residency | No | No | No | No | Yes | No |
| SLA | No | No | No | No | Yes | No |
| Support | Community | Email | Priority | Dedicated | Dedicated + SLA | Priority |

---

## Overage Pricing Table

| Plan | Overage Block Size | Overage Price |
|------|-------------------|---------------|
| Launch | N/A (no overage) | N/A |
| Starter | 50,000 credits | $15 |
| Growth | 250,000 credits | $40 |
| Scale | 1,000,000 credits | $150 |
| Enterprise | Custom | Custom |
| Founder | N/A (lifetime, no overage) | N/A |

---

## FAQ / Notes

- **What counts as a "page processed"?** Each URL in the uploaded CSV counts as one page, regardless of page size.
- **Do credits roll over?** No. Monthly credits reset on billing date. Founder lifetime credits never expire.
- **What happens when I exceed my limit?** You'll be prompted to purchase an overage block or upgrade your plan.
- **Can I downgrade?** Yes. Downgrade takes effect at the next billing cycle.
- **Is Quick Match really free?** Yes for paid plans (unlimited). Free trial users get 2,500 Quick Matches per month.
- **Can Launch users use Deep Match?** No. Deep Match requires a paid plan (Starter or above) or the Founding Migration License.
