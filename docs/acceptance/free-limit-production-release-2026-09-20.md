# Five-per-day free migration policy: production release

September 20, 2026. The owner approved five new free migrations per account in a
rolling 24-hour window. Matching quality and the 500-old-page allowance remain
unchanged. Replaying the same operation and worker retries do not consume another
slot. New execution keys count as new work; paid grants are independent.
Unfinished verification resumes unchecked URLs against the same export and allowance.

## Source and validation

- API commit `b11bb3627c703659e57dadac888bdd95526ddf8f`, retained on GitHub at
  `deploy/pivot-close-b11bb36`; independent ls-remote matched the exact SHA.
- Eight focused tests passed on disposable native PostgreSQL; the database was
  stopped and its port independently confirmed closed. See
  `free-migration-rolling-limit-2026-09-20.md` for concurrency, replay, deletion,
  isolation, paid-rights, expiry and verification-resume coverage.
- Diff whitespace check passed. Credential scan found only two explicitly local
  `fixture-only` PostgreSQL example URLs; no private key, Stripe key, GitHub token
  or JWT match. These examples were classified rather than called a zero-match scan.

## Ordered deployment

1. Render API `srv-d59il0shg0os73cctuug`, deployment
   `dep-dao0l0ek1f9s73a8cvpg`, showed Deploy succeeded / Live at source `b11bb36`.
   Build/deploy duration was 1m22s. Worker was not redeployed or restarted.
2. Production read-only preflight matched the existing function body exactly to
   reviewed migration 037, required completed migration 057, pending 058 history,
   exact database target, verified TLS and service-only execution privileges.
3. Applied `058_free_migration_rolling_limit.sql` once in one transaction, with
   history version `20260920163058`. Source SHA-256:
   `70408789b07959a6628b2476c9102c793ff60fa6a0a155ad257cbd63ab82764b`.
   Postflight independently read committed history and exact function body;
   owner/security-definer/search-path metadata stayed unchanged, anon and
   authenticated execution stayed denied, service_role execution stayed granted.
   Exclusive journal: `/private/tmp/redirx-operation-20260919/058-apply-journal.json`.
   No production test jobs were manufactured to fill the allowance.
4. Landing documentation commit `7f108e28a924449d280e0652d2ab1aec55406875`, retained
   on `deploy/mcp-landing-7f108e2`, refreshed the contract-derived guide and skill.
   Five existing document/route tests passed; Vercel deployment
   `dpl_A7gTWVV8jonawP2AkXgLkV4CpBeh` reached READY, its protected preview returned
   the expected documents, and promotion succeeded. Public `redirx.dev/llms.txt`
   and `/skill.md` returned 200 with text/plain and text/markdown respectively,
   the five-per-day rule, same-export verification retry policy, OAuth instructions
   and explicit test_only billing disclosure. Original dirty pricing/design work
   was not published or modified.

At 16:35 UTC a native MCP read through the new API still reported the existing paid
15k/20k capacity run `331c0ebc-271e-440f-b5fa-2af581d580b3` running at stage 3/6.
No duplicate or replacement run was started. This is continuity evidence, not
completed capacity acceptance. The native test proves sixth-admission rejection;
production proof here is exact deployed code/SQL and committed-history verification,
not a claim that six real production jobs were submitted.

Billing remains test_only. This release neither enables live charges nor changes
account settings, compute plans or recurring subscriptions.
