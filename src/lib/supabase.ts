/**
 * The browser's Supabase client, used for authentication only.
 *
 * Purpose: The browser may hold only the publishable key, and sessions use the PKCE flow with
 * automatic refresh so users stay signed in safely.
 * How it fits: Used by `auth/*`, the auth screens and `lib/api.ts` (for the access token); all
 * data goes through the backend API instead; layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { createClient } from '@supabase/supabase-js'
import { getEnv } from './env'

const env = getEnv()

export const supabase = createClient(env.supabaseUrl, env.supabasePublishableKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: 'pkce',
  },
})
