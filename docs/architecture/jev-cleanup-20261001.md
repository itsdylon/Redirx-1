# Jev cleanup — 2026-10-01

## Baseline and scope

Based on `3ddece141498cf90dd39267bdd8bee3a7c5eb725`, the latest Jev deployment
branch (`deploy/jev-mvp-3ddece1`) found in the local repository and confirmed
against GitHub branch tips on October 1. Live service SHAs were not queried.
The original `Redirx-1` checkout remains on `pivot/mcp-primary`, with its dirty
files preserved. Do not merge that older checkout over this release baseline.

The owner requested a smaller modular codebase and selected the latest shipped
Jev line as the cleanup baseline. This is the separate cleanup review anticipated
by the keep manifests, not a rollback of the product direction.

## Removed deliberately

The URL-only production adapter constructs `Page` values without content, uses
stage-two selection, and never calls the experiment's automatic decision policy.
A repository-wide caller search and before/after behavior comparison support:

- Remove unused Jev content fields, text tokenization/excerpts and content voters.
- Remove alternate stage-one/mean decision modes and threshold/GONE/split policy.
- Remove unused benchmark labels, alternate targets, cache-directory argument,
  example-count helper, retrieval constants and imports.
- Keep both model passes and every question unchanged, including existence and
  relation questions: changing them would change prompt/cache identity.
- Keep lexical/dense URL retrieval, seed calibration and analogical rewrites.

The algorithm now depends on two structural protocols in `src/redirx/jev/ports.py`:
judgment and embeddings. Existing provider/storage implementations remain injected
by the run adapter. No new plugin framework or model fallback was introduced.

The following frontend files were outside the import closure rooted at `main.tsx`,
all test files and test setup. Literal static/dynamic imports and repository
references were checked; no import glob or computed module loader was found.
They were removed, not moved into an archive in the application source:

- `frontend/src/components/ui/accordion.tsx`
- `frontend/src/components/ui/aspect-ratio.tsx`
- `frontend/src/components/ui/avatar.tsx`
- `frontend/src/components/ui/calendar.tsx`
- `frontend/src/components/ui/carousel.tsx`
- `frontend/src/components/ui/chart.tsx`
- `frontend/src/components/ui/command.tsx`
- `frontend/src/components/ui/context-menu.tsx`
- `frontend/src/components/ui/drawer.tsx`
- `frontend/src/components/ui/form.tsx`
- `frontend/src/components/ui/hover-card.tsx`
- `frontend/src/components/ui/input-otp.tsx`
- `frontend/src/components/ui/menubar.tsx`
- `frontend/src/components/ui/navigation-menu.tsx`
- `frontend/src/components/ui/popover.tsx`
- `frontend/src/components/ui/radio-group.tsx`
- `frontend/src/components/ui/resizable.tsx`
- `frontend/src/components/ui/scroll-area.tsx`
- `frontend/src/components/ui/sidebar.tsx`
- `frontend/src/components/ui/textarea.tsx`
- `frontend/src/components/ui/toggle-group.tsx`
- `frontend/src/components/ui/toggle.tsx`
- `frontend/src/components/AdminEmailTesting.tsx`
- `frontend/src/components/Header.tsx`
- `frontend/src/components/PreviewTable.tsx`
- `frontend/src/components/ProtectedRoute.tsx`
- `frontend/src/components/StatsSidebar.tsx`
- `frontend/src/components/ThemeToggle.tsx`
- `frontend/src/components/figma/ImageWithFallback.tsx`

Removed direct dependencies used only by that scaffolding:

`@radix-ui/react-accordion`, `@radix-ui/react-aspect-ratio`, `@radix-ui/react-avatar`, `@radix-ui/react-context-menu`, `@radix-ui/react-hover-card`, `@radix-ui/react-menubar`, `@radix-ui/react-navigation-menu`, `@radix-ui/react-popover`, `@radix-ui/react-radio-group`, `@radix-ui/react-scroll-area`, `@radix-ui/react-toggle`, `@radix-ui/react-toggle-group`, `cmdk`, `embla-carousel-react`, `input-otp`, `react-day-picker`, `react-hook-form`, `react-resizable-panels`, `recharts`, `vaul`.

The npm lock graph falls from 426 entries to 367, including its root entry; retained
package versions are unchanged. Vite aliases for removed packages were also removed.
This reduces source and dependency surface; it is not a measured JavaScript download
saving. The app still imports historical routes and the large-chunk warning remains.

## Protected boundaries

No endpoint, applied SQL migration, persisted table or historical record is removed.
The old content engine, subscription/Watch/verification paths, export formats,
OAuth and browser login flows remain. Jev marker dispatch, queue authority,
global budget accounting, cache identities and explicit review are unchanged.
Python Redis remains an environment-selected limiter backend; scikit-learn still
serves historical matching. They are not unused just because the new core avoids them.

The old root README and agent guide wrongly presented content/cosine matching as
the main product. They now identify the current flow, actual entry points and
invariants. Historical design/rollout evidence remains dated and linked.

## Verification

A stale worker test
fixture was reproduced failing on unmodified `3ddece1`: its authorization stub
returned None, and it did not stub the newly added Jev engine-marker lookup.
The fixture now returns the authorization envelope and explicitly selects the
historical engine, retaining its ordering and finalization assertions.

### Executed checks

- 80 deterministic before/after cases: identical rankings, calibration diagnostics,
  serialized model states/questions, selected targets, probability, token/latency
  accounting and stored candidate lists. Both seeded/unseeded and refusal outcomes
  exercised. This is behavior preservation, not model-quality evidence.
- 42 Python tests passed, none skipped: core contract, actual TypeSafe SDK mocked
  transport, database transport, native Jev lifecycle, worker compatibility,
  artifact decision projection and migration-run authority, plus the opt-in
  500-old/2000-new synthetic-provider capacity fixture. The fixture persisted 500
  mappings in 9.36 seconds for one concurrent run. No real model calls or billing.
- Clean `npm ci --ignore-scripts` succeeded against the reduced lockfile. Then all
  421 frontend tests across 30 files passed and the Vite production build passed.
  No full frontend TypeScript check or visual/browser acceptance was run. Vite's
  existing large-chunk warning remains (about 1.47 MB uncompressed JavaScript).
- `node scripts/check_pivot_contract.mjs` passed: nine tools, nine pricing fixtures,
  `test_only` historical pricing activation preserved.
- `git diff --check` passed. No production calls, schema changes, deployment,
  account-setting changes or edits to the original dirty working tree.

The tests used a disposable PostgreSQL server on 127.0.0.1:55487. The test classes
create/drop their own databases; that server was stopped after verification.

## Remaining work requiring separate evidence

1. Reconcile live per-service SHAs, deployment branches and the drifted Render
   blueprint into a trustworthy release inventory. No account settings changed here.
2. Make local process startup scoped to one worktree and allocate distinct ports;
   the existing `dev.py` kills by broad process patterns and omits MCP/auth startup.
3. Decide when legacy creation endpoints and UI funnels can retire. Existing
   historical review/download support is not equivalent to permission to delete
   every legacy engine or route. Trace external callers and retained jobs first.
4. Consolidate Python dependency declarations (`pyproject.toml` is incomplete
   relative to `requirements.txt`) with a clean-install packaging check. Do not
   trim transitive or environment-selected dependencies by text search alone.
5. Evaluate lazy loading historical frontend routes if initial bundle size becomes
   a priority. This change removes dead source but preserves route-loading behavior.
