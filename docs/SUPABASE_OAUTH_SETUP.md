# Supabase OAuth Setup (Google + GitHub)

This runbook configures OAuth for the frontend callback route:

- Local: `http://localhost:3000/auth/callback`
- Production: `https://<your-production-frontend-domain>/auth/callback`

## 1) Supabase URL Configuration

1. Open Supabase Dashboard.
2. Go to **Authentication → URL Configuration**.
3. Set **Site URL** to your production frontend origin (no trailing slash), for example:
   - `https://redirx.onrender.com`
4. Add these **Redirect URLs**:
   - `http://localhost:3000/auth/callback`
   - `https://<your-production-frontend-domain>/auth/callback`

## 2) Google OAuth Provider

1. In Google Cloud Console, create an **OAuth 2.0 Client ID** of type **Web application**.
2. Configure **Authorized JavaScript origins**:
   - `http://localhost:3000`
   - `https://<your-production-frontend-domain>`
3. Configure **Authorized redirect URIs**:
   - `https://<your-project-ref>.supabase.co/auth/v1/callback`
4. In Supabase Dashboard:
   - Go to **Authentication → Sign In / Providers → Google**
   - Enable Google
   - Paste Google client ID and client secret
   - Save

## 3) GitHub OAuth Provider

1. In GitHub Developer Settings, create a new **OAuth App**.
2. Set **Authorization callback URL**:
   - `https://<your-project-ref>.supabase.co/auth/v1/callback`
3. In Supabase Dashboard:
   - Go to **Authentication → Sign In / Providers → GitHub**
   - Enable GitHub
   - Paste GitHub client ID and client secret
   - Save

## 4) Identity Linking Policy

Use Supabase automatic linking behavior so the same verified email can resolve to a single user/profile across password + OAuth sign-ins.

## 5) Frontend Environment

Frontend uses:

```bash
VITE_SUPABASE_URL=https://<your-project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon-key>
```

OAuth redirect target is computed in-app as:

```ts
${window.location.origin}/auth/callback
```

No separate OAuth callback env var is required.

## 6) Smoke Test Checklist

1. From `/login`, click **Continue with Google** and complete sign-in.
2. From `/signup`, click **Continue with GitHub** and complete sign-in.
3. From `/quick-match`, use social buttons in the auth gate and verify return to `/quick-match`.
4. Confirm existing same-email user identity is reused (no duplicate account/profile).
5. Confirm `/api/auth/me` succeeds and profile loads after OAuth callback.
