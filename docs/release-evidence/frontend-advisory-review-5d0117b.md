# Frontend high/critical advisory review — 5d0117b

Reviewed September 20, 2026. Scope: deployed product revision
`5d0117bda961603987a6e5d212c38191ba61edc3`, compiled React browser application,
its source/import paths, committed frontend package/lock files, and the release
owner's read-only npm audit JSON. No dependency, lockfile, browser, service,
account-setting or production-data change was performed for this review.

**Conclusion:** no demonstrated high/critical exploit on the activated static
companion flow was identified. This permits continuing controlled pivot acceptance;
it is not a clean audit or evidence that vulnerable functions never execute.
The legacy XLSX import path needs remediation before it is made reachable again,
including through a flags-off rollback.

## Evidence and classification

The supplied audit reports **26 affected packages: 13 moderate, 11 high, 2 critical**.
Exit 1 denotes reported advisories, not failure to obtain the report. Source was
read from the integration checkout. Both `frontend/package.json` and its lockfile
were independently compared with `git show 5d0117b:<path>` and matched exactly.
Installed versions of all 13 high/critical packages also matched the committed lock.

The raw report is the release-local `frontend-audit-5d0117b.json`; this review does
not embed it or imply a new registry audit was run. Counts classify affected
packages, not unique CVEs or independent exploit paths.

Evidence SHA-256 values:

```text
frontend-audit-5d0117b.json
b364fa1e4e169a1c131e589a2d92815a7777dbdba05603ff489c09bc4e09b16a
frontend/package-lock.json
e5cbb9b264e208fad37a3cd72f588365e291c9cf5ad6e8c3d4b42bfabb6b688a
```

Only three audited nodes are marked exclusively `dev` by the lock: `vitest`,
`@vitest/mocker`, and `undici` (one critical, one moderate, one high). The remaining
23 are not exclusively dev (one critical, 12 moderate, 10 high).
`@tailwindcss/vite` is declared in production dependencies, so its Vite peer and
related build tooling remain in that tree. **An npm production-tree classification
is not equivalent to browser reachability.** No `npm audit --omit=dev` result is
claimed here; this split comes from the supplied report plus lock metadata.

## Critical packages

### protobufjs 7.5.4

Path: `posthog-js` → `@opentelemetry/exporter-logs-otlp-http` /
`@opentelemetry/otlp-transformer` → `protobufjs`.

The [maintainer code-execution advisory](https://github.com/protobufjs/protobuf.js/security/advisories/GHSA-xq3m-2v4x-88gg)
requires attacker-controlled protobuf schema/JSON-descriptor loading and the
resulting code-generation path. Ordinary use of trusted application schemas does
not meet that prerequisite. The separate
[bytes-default conversion advisory](https://github.com/protobufjs/protobuf.js/security/advisories/GHSA-66ff-xgx4-vchm)
also requires malicious schema metadata and generated conversion code.

The installed transformer has pre-generated trusted JavaScript schema in
`frontend/node_modules/@opentelemetry/otlp-transformer/build/esm/generated/root.js`,
whose import is `protobufjs/minimal`. The minimal entry exposes readers/writers,
utilities and RPC, not reflection schema parsing. Its browser HTTP log exporter
(`.../exporter-logs-otlp-http/build/esm/platform/browser/OTLPLogExporter.js`)
selects `JsonLogsSerializer`. No frontend application schema-loading call was found.

These source paths do not establish the attacker-schema prerequisite for the
critical advisory. This does not dismiss every protobuf advisory: decoder,
recursion and UTF-8 issues have their own conditions. No browser module-load or
function-execution instrumentation was performed, and no tree-shaking absence
claim is made.

### vitest 4.0.18

The [maintainer advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp)
concerns a listening Vitest UI/API server. The frontend is compiled by `vite build`
and published as static browser assets, not served by Vitest. The source test
scripts and `frontend/vite.config.ts` belong to local/CI use. This is relevant to
exposed developer/test environments, not an identified production static-site
request path. A dependency bump is still worthwhile separately.

## High packages

| Package/version | Source-level assessment |
| --- | --- |
| `react-router` / `react-router-dom` 7.10.1 | [main.tsx](../../frontend/src/main.tsx) uses `createRoot` and declarative `BrowserRouter`; [App.tsx](../../frontend/src/App.tsx) uses ordinary `Routes`/`Route`. No Framework Mode, data loaders/actions, RSC, SSR hydration or `ScrollRestoration` is wired. Framework/SSR advisories do not apply to this inspected architecture. |
| `xlsx` 0.18.5 | A genuine arbitrary-file parser exists in [fileParsers.ts](../../frontend/src/utils/fileParsers.ts): `parseXlsx()` calls `XLSX.read()` on the uploaded array buffer. The [validation](../../frontend/src/utils/validation.ts) file-size cap does not prove safety. Its only production UI caller is `UploadPage`, reached through legacy upload/Quick Match routes. Pivot routing intercepts those routes; see the rollback restriction below. |
| `lodash` 4.17.21 | Arrives through Recharts. No application `template`, `unset` or `omit` use was found. Inspected Recharts `omit` calls use fixed keys (`children`, `width`), not attacker-controlled path arrays; its source has no identified `template` call. |
| `vite` 6.3.5, `rollup` 4.53.3, `postcss` 8.5.6, `picomatch` 4.0.3, `nanoid` 3.3.11 | Vite/Tailwind build and development dependencies by function, even where the lock lacks `dev:true`. No application imports or browser processing of attacker-supplied CSS/globs were found. Build-environment inputs and exposed dev servers remain distinct risks; no such browser runtime service is shipped by this static build. |
| `undici` 7.22.0 | Comes through test-only `jsdom`; the deployed browser uses browser networking, not this Node HTTP client. |
| `ws` 8.18.3 | Comes through Supabase Realtime. The installed `@supabase/realtime-js/dist/module/lib/websocket-factory.js` selects the native browser WebSocket constructor; the npm `ws` parser is not the inspected browser transport. |

React Router's [open-redirect XSS advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-2w69-qvjg-hvjx)
explicitly excludes declarative `BrowserRouter`. Its
[manifest denial-of-service advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-chx6-hx7r-mcp5)
is Framework Mode only; the
[ScrollRestoration advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-8v8x-cx79-35w7)
requires the SSR feature absent here.

The separate [navigation backslash advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6)
must not be dismissed merely because the app uses browser rendering.
[authRedirect.ts](../../frontend/src/lib/authRedirect.ts) rejects raw/decoded
backslashes, protocol-relative paths, control characters and foreign origins.
Login/signup/callback consume that sanitizer; other inspected navigation targets
are fixed or locally prefixed. No application bypass was demonstrated. This is
source review, not an exploit test against the deployed browser.

The [Lodash code-injection advisory](https://github.com/lodash/lodash/security/advisories/GHSA-r5fr-rjxr-66jc)
requires attacker-controlled template-import names. That input path was not
identified in the application's Recharts use.

## XLSX rollback restriction

SheetJS documents [prototype pollution while reading crafted files](https://cdn.sheetjs.com/advisories/CVE-2023-30533)
and [regular-expression denial of service](https://cdn.sheetjs.com/advisories/CVE-2024-22363).
The installed 0.18.5 predates both fixes. The ordinary npm `xlsx` package does not
supply the maintained fixed releases; remediation needs a reviewed maintained
source/package decision, not an assumption that `npm audit fix` will solve it.

With the pivot enabled, [getPivotEntryRedirect](../../frontend/src/routes.tsx) and
[App](../../frontend/src/App.tsx) redirect both `/upload` and `/quick-match` before
rendering `UploadPage`, for signed-in and signed-out users. No companion XLSX input
caller was found. The parser can still be bundled because the legacy components
are statically imported: **unreachable UI is not absent dependency code**.

Do not re-enable browser XLSX uploads, including by disabling the frontend pivot
flag, without fixing/replacing that parser or an explicitly reviewed containment
change. Preserve existing purchase/history/review access through compatible code;
a flags-off rollback is not automatically an acceptable security rollback.

## Limits

- This is source wiring plus exact version/lock verification, not runtime tracing,
  proof of function non-execution, or a penetration test. `process.moduleLoadList`
  was not used as userland dependency evidence.
- The 13 moderate packages were not comprehensively assessed. In particular,
  PostHog/DOMPurify-related paths need their own input/configuration review before
  making an all-advisories safety claim.
- A static browser app can still have exploitable JavaScript; static hosting alone
  is not the reason to dismiss the router, parser or schema-generation issues.
- No fixes, lockfile changes, production exploit probes, account changes or release
  rollback were performed. This report authorizes none of those actions.
