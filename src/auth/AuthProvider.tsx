/**
 * Provides the Supabase session to the app and signs users out.
 *
 * Purpose: The app must know who is signed in before it renders protected routes, follow sign-in,
 * sign-out and password-recovery events, and never keep one user's cached data after sign-out.
 * How it fits: Mounted by `App.tsx` at the router root; exposes `AuthContext` (read with
 * `useAuth`) to the route guards, `OrgProvider` and the screens; layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '@/lib/supabase'
import { AuthContext, type AuthContextValue } from './useAuth'

/** Tracks the Supabase session and shares it, with `signOut`, through `AuthContext`. */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    // getSession() waits for client initialisation, which includes the PKCE
    // code exchange when the page was opened from an auth redirect.
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return
      setSession(data.session)
      setLoading(false)
    })

    const { data } = supabase.auth.onAuthStateChange((event, nextSession) => {
      // Keep this callback synchronous: awaiting Supabase calls here can deadlock the auth lock.
      if (!active) return
      setSession(nextSession)
      setLoading(false)
      if (event === 'PASSWORD_RECOVERY') navigate('/reset-password', { replace: true })
      if (event === 'SIGNED_OUT') queryClient.clear()
    })

    return () => {
      active = false
      data.subscription.unsubscribe()
    }
  }, [navigate, queryClient])

  /** Signs out everywhere (or at least on this device), clears cached data and goes to the login page. */
  const signOut = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    // A failed global revoke (e.g. already-expired token) still needs to clear this device.
    if (error) await supabase.auth.signOut({ scope: 'local' })
    queryClient.clear()
    navigate('/login', { replace: true })
  }, [navigate, queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({ session, user: session?.user ?? null, loading, signOut }),
    [session, loading, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
