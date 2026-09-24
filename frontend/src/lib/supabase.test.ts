import { expect, it, vi } from 'vitest';

// Implicit flow puts live tokens in the callback URL; see supabase.ts.
it('uses the PKCE flow so the OAuth callback never carries tokens in its URL', async () => {
  vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'fixture-anon-key');
  const { supabase } = await import('./supabase');
  expect((supabase.auth as unknown as { flowType: string }).flowType).toBe('pkce');
  vi.unstubAllEnvs();
});
