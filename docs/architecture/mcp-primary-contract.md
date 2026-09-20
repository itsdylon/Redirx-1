# MCP-primary contract, version 1

Status: implementation contract; not a production capability announcement.
Policy activation: test-only until the commercial choices below are resolved.

This is P00 of the September 17 pivot. It supersedes conflicting Quick/Deep Match,
free-unlimited-matching/paid-export, and legacy graduated-price instructions in
`agentic-pivot.md`, PRICING_V2/V3 documents, and CLAUDE.md for new pivot work.
Existing production endpoints and paid rights remain compatible until their
replacement packets are integrated. Do not advertise the new tools before P15.

`contracts/pivot-v1.json` is the machine-readable source for names, statuses,
limits, price bands and boundary examples. Python policy tests and the Node
contract check consume this exact file. It does not turn on new pricing.

## Domain identity

A migration belongs to one user and one old-site/new-site pair. A site records
its public origin and any explicitly declared staging/live aliases. Never infer
that www/non-www, subdomains, or unrelated staging hosts are interchangeable.
Persist a normalized identity for billing separately from the original URLs
needed to produce redirect rules. Preserve meaningful query strings, path case,
escaping and slash variants; drop fragments. Any query/asset exclusion policy is
recorded in the inventory and included in its version/hash.

Legacy `migration_sessions.id` remains a run identifier and its review links
continue to work. The new durable `migration_id` must not be interpreted as an
existing session ID. Additive `/api/v2` resources avoid that ambiguity. v1 remains
available until compatibility policy and old callers are checked; both versions
eventually call the same service layer and entitlement rules.

An inventory snapshot records migration, side, original URLs, canonical counting
keys, source provenance, completion status, exclusions, content hash, captured
time and policy version. Quotes bind to a complete old-site inventory snapshot.
Partial/blocked discovery needs additional input; its count is not a final quote.
The new-site inventory binds matching input without affecting commercial price.

Runs bind immutable old/new inventory IDs. An explicit rerun creates a new run;
replaying an operation's idempotency key returns the original result. Artifacts
bind a run and persisted decision revision, selected mappings, format, hash and
target origins. Verification and monitoring bind the exported/deployed artifact,
not the latest mutable matches.

## Public operation envelope

All new tool results carry structured content plus a short readable summary:

```json
{
  "contract_version": "1.0.0",
  "migration_id": "uuid-or-null-for-account-actions",
  "operation_id": "uuid-or-null-for-synchronous-reads",
  "status": "running",
  "next_action": "poll",
  "retry_after_seconds": 10,
  "progress": {"completed": 40, "total": 500, "complete": false},
  "data": {},
  "error": null
}
```

Use UUIDs for durable object IDs and ISO-8601 UTC timestamps. Omit irrelevant
optional fields; `data` is always an object. `status` and `next_action` come from
the shared contract. Terminal operation status does not imply the entire migration
is complete: a matching operation can succeed before deployment or verification.
`partial` is terminal for that attempt but explicitly incomplete in coverage.
Polling recommends bounded backoff; a 429 includes retry timing, not a smaller
quality tier. Raw tokens, HTML, secrets and full inventories stay out of responses.

Errors use `code`, `message`, `retryable`, `next_action`, and optional safe
`details`. Codes: `not_found`, `invalid_input`, `inventory_incomplete`,
`capacity_exceeded`, `quote_expired`, `payment_required`, `allowance_exhausted`,
`revision_conflict`, `operation_conflict`, `not_ready`, `rate_limited`,
`reconnect_required`, `origin_unavailable`, `internal_error`. Ownership failures
use not-found without revealing another account's objects. Payment is a business
state, not proof supplied by the caller or a checkout success URL.

Payment-required data contains `quote_id`, `policy_version`, `old_pages`,
`amount_cents`, `currency`, `expires_at`, `checkout_url`, and the pending operation
ID. Custom quotes use null amount and `request_custom_quote`, never a zero price.
After payment, status/poll/retry resumes the same reserved operation. Stripe's
verified webhook and server-side grants are authoritative.

## Tool parameters and semantics

Required parameters and REST mappings are in the JSON contract. Additional fields:

- `plan_migration`: old/new sites are origin strings. Optional name, staging/live
  aliases, imported inventory handles and GSC property. Returns durable migration
  and inventory/operation IDs. A repeated idempotency key with changed input is
  `operation_conflict`, not silently accepted.
- `run_migration`: inventory IDs are `{old, new}`. Optional quote/grant ID and
  `rerun_of` run ID. No public pipeline choice. New work requires entitlement before
  content fetching. Network retries and worker retries never debit twice.
- `get_migration`: optional operation/run ID, otherwise returns latest summary plus
  durable references, quote/grant state and actionable next step.
- `list_matches`: optional opaque cursor, limit (default 100, max 500), filter
  (`all`, `needs_review`, `unmatched`, `approved`, `rejected`) and traffic ordering.
  Stable ordering with a tiebreaker; response has items and next cursor. Metrics
  distinguish absent data from observed zero.
- `resolve_matches`: at most 100 decisions. Each carries mapping ID, expected
  revision, action (`accept_repair`, `set_target`, `approve`, `reject`, `defer`,
  `intentional_removal`), optional target URL and rationale. Server validates
  evidence/target scope and returns per-row outcomes; ambiguous semantic decisions
  are not silently accepted. Record actor/reason/version in a shared write service.
- `export_redirects`: optional explicit `allow_partial=false`; only validated/
  approved mappings by default. Partial exports explain exclusions. Use path
  matching syntax separately from destination-origin handling; no universal
  `url_format=paths` transformation of both sides. Return artifact metadata and an
  authenticated bounded download, with platform installation instructions.
- `verify_redirects`: optional deployment confirmation with live origins and
  artifact revision. Starts a persisted one-shot job. No subscription required for
  the included check; interrupted attempts resume without consuming twice.
- `manage_monitoring`: action `start`, `pause`, `resume`, or `cancel`; start requires
  artifact ID and established site grant. Optional alert address. Pause/resume
  never resets entitlement clocks. Treat renewal consent separately from start.
- `get_monitoring_status`: coverage, last complete sweep, next check, grant expiry,
  issues and their pagination; never equate unchecked with healthy.
- `get_monitoring_fixes`: optional cursor/limit; returns issue IDs, evidence,
  expected mappings and an immutable correction-artifact reference when ready.
  Generating a fix never marks it deployed or resolved.
- `connect_search_console`: action `connect`, `status`, `properties`, `disconnect`,
  or `sync`. Connect returns a browser consent handoff. Sync requires migration/
  property and optional data window. Mutating actions accept idempotency keys;
  no Google credentials are collected from the agent.

REST details that do not need separate MCP tools: POST inventory imports under a
migration, GET operation status, POST quote checkout, GET authorized artifacts, and
POST deployment confirmation. These resources share the same envelope/auth rules.

## Commercial defaults and launch gates

The JSON policy uses 500-free/$49/$99/$199 bands on old pages only.
The owner confirmed the 500-page free ceiling and activation of included monitoring
within 90 days of purchase on 2026-09-19. Studio's 15,000-page cap was also confirmed.
Small sites get full quality, export and one post-launch check. Paid migrations
include 30 rerun days from the first successful paid run and 30 monitoring days
from explicit deployment confirmation. Clocks are immutable; no implicit paid
renewal. Requotes for growth/new scope are explicit. Purchased artifact downloads
outlive the rerun window. Studio's five migration slots use the billing period;
monitoring slots measure concurrent sites. Reruns/downloads don't consume slots.

Unresolved capacity choices remain enumerated in the shared JSON. The owner-approved
free-use policy permits five new free execution reservations per account in a rolling
24-hour window. Replaying the same operation or retrying its worker job consumes no
additional slot; a fresh execution with a new key counts as new work. Paid grants are
outside this free-use limit. The limit must be enforced atomically in durable storage,
with a retry time when full; it does not reduce the 500-page allowance or matching quality.
Unfinished verification resumes unchecked URLs against the same immutable export
without consuming another included verification. Implementation/deployment receipts
determine whether these contract requirements are live.
Studio supports self-serve migrations through 15,000 old pages; larger migrations
require a custom quote. Confirmed commercial terms do not by themselves activate
live billing: provider and combined workflow acceptance remain release gates.

## Verification and release boundary

Run `python -m unittest backend.tests.test_pivot_policy` and
`node scripts/check_pivot_contract.mjs`. The checks exercise the same boundary
fixtures from Python and JavaScript. Subsequent packets must add response/transport
contract tests as endpoints land and consume this contract instead of duplicating
commercial values. Provider configuration, Stripe live catalog, full capacity,
real-client consent and production deployment remain P19 release work.
