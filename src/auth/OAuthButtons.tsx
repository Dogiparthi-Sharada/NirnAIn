/**
 * "Continue with Google / Microsoft" buttons and the "or … with email" divider of the sign-in and
 * create-account screens (mockup views/auth.js).
 *
 * Starts Supabase OAuth (`signInWithOAuth`, PKCE) and remembers `next` in session storage so
 * `AuthCallback` can send the user on after the provider round trip; the redirect URL itself stays
 * exact, as the providers require. Errors are mapped to friendly copy and handed to the screen.
 */
import { useState } from 'react'
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

/** The two provider buttons; while one is redirecting both are disabled. */
export default function OAuthButtons({ next, disabled, onPendingChange, onError }: Props) {
  const [pending, setPending] = useState<OAuthProvider | null>(null)

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
      {PROVIDERS.map((provider) => (
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

/** The "or sign in with email" rule between the provider buttons and the email form. */
export function Divider({ label }: { label: string }) {
  return <div className="divider">{label}</div>
}
