import { createClient } from '@supabase/supabase-js';
import { AUTH_STORAGE_KEY } from './authSessionStorage';

// Get Supabase credentials from environment variables
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'http://127.0.0.1:54321';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || '';

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  // Keep SDK defaults (including auto-refresh and callback detection). It is the
  // sole refresh owner; AuthContext mirrors SDK changes for legacy API callers.
  auth: { storageKey: AUTH_STORAGE_KEY },
});
