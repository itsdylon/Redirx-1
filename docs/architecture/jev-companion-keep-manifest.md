# Jev companion and MCP preservation manifest

The free Jev workflow adds two native MCP tools to the existing authenticated pivot surface: `import_inventory` and `refine_matches`. The exact published schemas live in `mcp-server/src/tools/pivot.ts`; method/path mappings are in `contracts/pivot-v1.json`, with the Jev workflow additions in `contracts/jev-mvp-v1.json`.

Keep these dependencies when retiring an old matching pipeline:

- `mcp-server/src/tools/context.ts`, backend client, identity resolution and auth adapters: bind each call to the current account's delegation, never forward the provider token as a backend credential.
- `mcp-server/src/mcpServer.ts` and resource registration: preserve feature-gated legacy tools, telemetry context and owner-authorized artifact downloads. The gateway never follows arbitrary artifact URLs.
- `frontend/src/api/config.ts`, auth provider, `ToolLayout` and route guards: preserve browser ownership and deep-link sign-in behavior.
- `frontend/src/api/pivot.ts` and `components/pivot/useRequestScope.ts`: preserve actual API errors, aborts, idempotency identity and optimistic revision checks.
- `PivotCompanionPage`, `PivotMigrationDetail`, UI primitives: preserve migration history, paginated mapping review, exact run ID, explicit decisions, and artifact downloads. A Jev proposal is never an approval: confirmation sends `set_target`, target URL, mapping ID and expected revision. Refinement sends the same run ID and the current expected seed revision; retries reuse the same operation key.
- `SubscriptionCheckoutPanel`, `PivotBillingNotice`, `SearchConsolePanel`, existing checkout API methods, billing return route and monitoring panel: retained for historical records and existing flows. New Jev runs hide paid offers and monitoring panels; the history page does not solicit new subscriptions. Optional legacy Search Console remains available on non-Jev details.

Plan does not import lists. `import_inventory({migration_id,side,urls,idempotency_key})` calls the actual owned importer with `{side,rows:urls,idempotency_key}`. A usable result is `data.inventory.status='complete'`; use `data.inventory.id` as the run's corresponding inventory ID. No crawler substitutes for this import. Partial input must be corrected, using a new key.

`run_migration` forwards optional explicitly verified `{old_url,new_url}` pairs (at most100) to backend inventory/ownership validation. The UI calls no provider directly. Free bounds are500 old URLs,2000 new URLs,2MiB combined URL text,five new runs per24hours,and three total passes. Backend quota, revision and ownership checks remain authoritative.

Validation:20 native MCP SDK tests, including authenticated import shape/readiness, bounded rejection without HTTP, confirmed-pair forwarding and refinement retries;44 focused frontend/API tests including explicit Jev confirmation, no automatic approval, stale proposals, three-pass boundary and failed-pass resume, same-key refinement retry, plus legacy payment/consent and paginated review regressions. Gateway TypeScript build and frontend Vite production build passed. Vite is not a full frontend typecheck; it reports an existing large-chunk warning. No provider calls or production deployment in this packet.
