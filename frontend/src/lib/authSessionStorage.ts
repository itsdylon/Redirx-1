// Match Supabase's default key explicitly so failed network sign-out can clear
// only this application's persisted SDK session, never another application's.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'http://127.0.0.1:54321';
export const AUTH_STORAGE_KEY = `sb-${new URL(supabaseUrl).hostname.split('.')[0]}-auth-token`;
export const AUTH_CLEARED_EVENT = 'redirx:auth-cleared';

export function clearBrowserSession(notify = true): void {
  for (const key of [
    'access_token', 'refresh_token', AUTH_STORAGE_KEY,
    `${AUTH_STORAGE_KEY}-code-verifier`, `${AUTH_STORAGE_KEY}-user`,
  ]) localStorage.removeItem(key);
  if (notify) window.dispatchEvent(new Event(AUTH_CLEARED_EVENT));
}
