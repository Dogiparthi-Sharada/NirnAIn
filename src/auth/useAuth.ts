/**
 * The auth context and the `useAuth` hook that reads it.
 *
 * Purpose: Kept apart from `AuthProvider.tsx` so components can read the session without importing
 * the provider (and so fast refresh keeps working).
 * How it fits: `AuthProvider` fills the context; guards, `OrgProvider`, the shell and the screens
 * read it; layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { createContext, useContext } from 'react'
import type { Session, User } from '@supabase/supabase-js'

export interface AuthContextValue {
  session: Session | null
  user: User | null
  /** True until the initial session (including any URL code exchange) has been resolved. */
  loading: boolean
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

/** Returns the session state; throws when used outside `<AuthProvider>`. */
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
