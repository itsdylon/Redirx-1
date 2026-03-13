
  # RedirX Wireframe

  This is a code bundle for RedirX Wireframe. The original project is available at https://www.figma.com/design/FkAjqjUxpPLO5bzUxuvwiX/RedirX-Wireframe.

  ## Running the code

  Run `npm i` to install the dependencies.

  Run `npm run dev` to start the development server.

  ## Frontend environment variables

  - `VITE_SUPABASE_URL` (required): Supabase project URL for client auth.
  - `VITE_SUPABASE_ANON_KEY` (required): Supabase anon/public key.
  - `VITE_API_BASE_URL` (optional): API origin. Defaults to same-origin.
  - `VITE_CONTENT_MAX_URLS_PER_SITE` (optional): Deep Match per-file URL cap used for pre-submit UX blocking. Defaults to `5000`.

  OAuth callback URL is computed as `${window.location.origin}/auth/callback`.
  Configure Supabase + provider settings using [`docs/SUPABASE_OAUTH_SETUP.md`](../docs/SUPABASE_OAUTH_SETUP.md).
  
