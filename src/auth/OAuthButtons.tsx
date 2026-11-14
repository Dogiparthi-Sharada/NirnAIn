/**
 * "Continue with Google / Microsoft" buttons and the "or … with email" divider of the sign-in screens.
 *
 * Purpose: Only providers the auth server has enabled may be offered: a disabled provider answers
 * with a raw JSON error page (QA P-08), so those buttons, and the divider when none is left, are
 * hidden until GoTrue's public settings list them. The provider redirect URL stays exact, so
 * `next` waits in session storage during the round trip.
 * How it fits: Used by `screens/Login.tsx` and `screens/Signup.tsx`; starts Supabase OAuth (PKCE)
 * and maps errors through `auth/errors.ts`; `screens/AuthCallback.tsx` finishes the round trip;
 * layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getEnv } from '@/lib/env'
import { supabase } from '@/lib/supabase'
import { authRedirectUrl, rememberNext } from '@/lib/redirect'
import { friendlyAuthError } from './errors'

export type OAuthProvider = 'google' | 'azure'

interface Props {
  /** Where to land after the provider round-trip (already validated). */
  next: string
  disabled?: boolean
  onPendingChange?: (pending: boolean) => void
  onError: (message: string) => void
}

const PROVIDERS: { id: OAuthProvider; label: string; glyph: string; color: string }[] = [
  { id: 'google', label: 'Google', glyph: 'G', color: '#4285F4' },
  { id: 'azure', label: 'Microsoft', glyph: '\u25A6', color: '#00A4EF' },
]

// The auth server's provider switches change rarely: read them once per session.
const SETTINGS_STALE_MS = 10 * 60_000

/** GoTrue's public settings: which external sign-in providers are switched on. */
async function fetchEnabledProviders(signal: AbortSignal): Promise<OAuthProvider[]> {
  const { supabaseUrl, supabasePublishableKey } = getEnv()
  const response = await fetch(`${supabaseUrl}/auth/v1/settings`, {
    headers: { apikey: supabasePublishableKey },
    signal,
  })
  if (!response.ok) throw new Error(`Auth settings answered ${response.status}`)
  const settings = (await response.json()) as { external?: Partial<Record<OAuthProvider, boolean>> }
  return PROVIDERS.map((provider) => provider.id).filter((id) => settings.external?.[id] === true)
}

/** The providers the auth server has enabled; empty while loading or when it can't be asked. */
function useEnabledProviders(): OAuthProvider[] {
  const settings = useQuery({
    queryKey: ['auth', 'providers'],
    queryFn: ({ signal }) => fetchEnabledProviders(signal),
    staleTime: SETTINGS_STALE_MS,
    retry: 1,
  })
  return settings.data ?? []
}

/** The enabled provider buttons; while one is redirecting all are disabled. */
export default function OAuthButtons({ next, disabled, onPendingChange, onError }: Props) {
  const [pending, setPending] = useState<OAuthProvider | null>(null)
  const enabled = useEnabledProviders()

  /**
   * Starts the OAuth redirect for `provider`; on failure clears the busy state and reports a friendly error.
   */
  async function start(provider: OAuthProvider) {
    setPending(provider)
    onPendingChange?.(true)
    rememberNext(next)
    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: authRedirectUrl(),
        scopes: provider === 'azure' ? 'email openid profile' : undefined,
      },
    })
    // On success the browser is already navigating to the provider; keep the busy state.
    if (error) {
      setPending(null)
      onPendingChange?.(false)
      onError(friendlyAuthError(error).message)
    }
  }

  return (
    <>
      {PROVIDERS.filter((provider) => enabled.includes(provider.id)).map((provider) => (
        <button
          key={provider.id}
          className="btn sec block oauth"
          type="button"
          onClick={() => void start(provider.id)}
          disabled={disabled || pending !== null}
          aria-busy={pending === provider.id || undefined}
        >
          <b style={{ color: provider.color }} aria-hidden="true">
            {provider.glyph}
          </b>{' '}
          {pending === provider.id ? `Opening ${provider.label}…` : `Continue with ${provider.label}`}
        </button>
      ))}
    </>
  )
}

/** The "or sign in with email" rule between the provider buttons and the email form (hidden without them). */
export function Divider({ label }: { label: string }) {
  const enabled = useEnabledProviders()
  if (enabled.length === 0) return null
  return <div className="divider">{label}</div>
}
