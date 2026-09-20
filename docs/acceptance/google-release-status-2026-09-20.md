# Google release status — 2026-09-20

Authenticated read-only Google Cloud inspection of project `redirx-490118`:

- Audience: External, In production. UI shows lifetime cap0/100 for unapproved scopes; this does not prove the requested scope is approved.
- Verification Center: branding is not shown; data verification not required because configured scopes are empty.
- Data Access: no non-sensitive, sensitive or restricted scope rows.
- Search Console API `searchconsole.googleapis.com`: Enabled.
- OAuth client `931201029817-a3aqrdrkr7t0j8iqhqh8g04ku7uqibub.apps.googleusercontent.com` matches the live native MCP connection URL exactly.
- Actual requested scopes: `openid email https://www.googleapis.com/auth/webmasters.readonly`; prompt consent, offline access.
- Registered callback includes the exact live `https://redirx-api.onrender.com/api/gsc/callback`, plus existing localhost/Supabase callbacks. No callback/client/secret changed.
- Existing main connection remains connected; actual native properties call returns owned `sc-domain:redirx.dev` and accessible DHS property. No disconnect or refresh revocation.

A scoped free-account connect operation generated a consent URL for configuration inspection. Selected the already-signed-in Google account and stopped before Continue/consent. No consent approval or connection replacement performed. URL/state retained only in tool memory; do not persist them. This existing Google account cannot establish new-user verification behavior.

Owner action pending: align configured Data Access scopes with the three requested scopes, then inspect whatever Verification Center requires. User was asked through asynchronous input; their account/project settings rule applies. Existing connection success is not public new-user approval evidence. Google guidance: https://developers.google.com/workspace/guides/configure-oauth-consent .

This is an optional Search Console release dependency; actual non-GSC free and paid journeys already pass. It is not a reason to declare the entire product broken or silently remove Search Console from the approved scope.

## Owner update independently verified

Dylon added the requested scopes. A fresh page reload now shows `openid`, `.../auth/userinfo.email`, and `.../auth/webmasters.readonly` under non-sensitive scopes, with no sensitive/restricted rows. Verification Center explicitly says data-access verification is not required. This closes the declared-scope/verification discrepancy. Branding is still not shown to users; do not describe the app as brand-verified. No additional data-access verification submission is required by the inspected current console. Root changed no Google setting.
