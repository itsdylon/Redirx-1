# Tom Hall Outreach Plan (Human + Feedback-First)

Date: 2026-03-13
Contact: tom.hall@sharpahead.com

## Quick Facts To Reference
- He has run at least two sessions.
- One project appears to be around 2.1k-2.5k URLs.
- Another appears to be around 250 URLs.
- Deep Match per-file cap is 5,000 URLs, so both are in range.

## Suggested Email (Authentic Tone)
Subject: Thanks for trying RedirX + 2 free Deep Match codes

Hey Tom,

Thanks for checking out RedirX.

I’m still in the early stages of building this tool, and I’d really value your feedback from the 2 mappings you ran today.

I made two 100% off codes for your current scans:
- `<<CODE_FOR_LARGER_SCAN>>`
- `<<CODE_FOR_SMALLER_SCAN>>`

Quick heads up: the larger site (around ~2.5k links) may take 30-40 minutes because Deep Match is content-based and does a deeper pass to improve accuracy.

If you’re open to it, I’d really love to know:
- How did you find RedirX?
- What migration are you working on?
- What made you come back for a second scan?

If you want to help shape the product direction, I’d also be glad to do a short 15-minute call.

Thanks again,
Dylon

## 24h Follow-Up (Short)
Subject: Quick follow-up on your Deep Match runs

Hey Tom,

Quick follow-up in case this got buried.

If you get a chance to run those two scans, even short notes on these three would help a lot:
- how you found RedirX
- what migration you’re solving
- what made you come back for a second run

If easier, happy to do a quick 15-minute call instead.

Thanks,
Dylon

## Question Priority

### Must-Ask First (Highest Signal)
1. How did you find RedirX?
2. What migration are you working on?
3. What made you come back for a second scan?

### Ask Next If He Engages
4. Which step took longer than expected?
5. What would make you trust match quality enough to ship?
6. What output format do you actually need (CSV, htaccess, JSON, Cloudflare bulk redirects)?

### Nice-To-Have
7. How many migrations does your team handle per month/quarter?
8. What are you using today instead?

## Call Incentive Recommendation
- Yes, offer an incentive for a short call.
- Keep it product-native: offer `1-2 additional 100% off Deep Match runs` (or equivalent free unlocks), not cash.
- Best timing: mention the call first; add incentive as a thank-you, not as the lead.

Suggested line:
- "If you’re open to a 15-minute feedback call, I can comp a couple more Deep Match runs as a thank-you."

## Boundary / Disclaimer Line
Use this one-liner in outreach:
- "For transparency: these codes are for your own runs in-app; we won’t run anything on your behalf unless you ask us to in writing."

## Sender Setup Notes (Dylon@RedirX.dev)
If sending via the app/Resend pipeline:
1. Set `EMAIL_FROM_ADDRESS` to `Dylon @ RedirX <dylon@redirx.dev>`.
2. Ensure `RESEND_API_KEY` is set in API + Worker env.
3. Redeploy services so env changes take effect.
4. Send a test from admin email testing endpoint/UI before reaching out.

If sending manually from an inbox (not app-generated):
- You need an actual mailbox for `dylon@redirx.dev` (Google Workspace/Fastmail/Zoho/etc.).
- Resend domain verification alone does not create a human inbox.
