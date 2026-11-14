/**
 * `/auth/callback`: where OAuth, email-confirmation, magic-link and password-recovery redirects
 * land. The Supabase client (`detectSessionInUrl` + PKCE) exchanges the `?code=` on start-up; this
 * screen waits for that, then sends the user on to `?next=` (or the `next` remembered before an
 * OAuth round trip), validated by `safeNext`.
 *
 * A code that can't be exchanged here was opened in another browser than the one that started the
 * flow: for sign-up that still means the email is confirmed (the user just signs in); for a
 * password reset the link must be requested again from this device.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { supabase } from '@/lib/supabase'
import { safeNext, takeRememberedNext } from '@/lib/redirect'
import AuthLayout from '@/auth/AuthLayout'
import { FullPageSpinner } from '@/components/ui'

/** Reads `error_description` from the query string or hash (Supabase uses both). */
function readUrlError(): string | null {
  const query = new URLSearchParams(window.location.search)
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const description = query.get('error_description') ?? hash.get('error_description')
  const code = query.get('error') ?? hash.get('error')
  if (!description && !code) return null
  return (description ?? code ?? '').replace(/\+/g, ' ')
}

/** A spinner while the session is established, then a redirect or an explanation. */
export default function AuthCallback() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const [confirmedElsewhere, setConfirmedElsewhere] = useState(false)
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const urlError = readUrlError()
    const remembered = takeRememberedNext()
    if (urlError) {
      setError(urlError)
      return
    }
    const next = safeNext(new URLSearchParams(window.location.search).get('next') ?? remembered)

    // Supabase only issues `?code=` after it has verified the email, so a code
    // that can't be exchanged here means the link was opened in a different
    // browser than the one that signed up: the email *is* confirmed.
    const hadCode = new URLSearchParams(window.location.search).has('code')

    void supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        navigate(next, { replace: true })
      } else if (hadCode && next === '/reset-password') {
        setError(
          'Password reset links must be opened in the same browser you requested them from. Request a new link from this device to continue.',
        )
      } else if (hadCode) {
        setConfirmedElsewhere(true)
      } else {
        setError(
          "We couldn't complete sign-in from this link. It may have expired or already been used. If you just confirmed your email, you can sign in now.",
        )
      }
    })
  }, [navigate])

  if (confirmedElsewhere) {
    return (
      <AuthLayout id="v-callback" title="Your email is confirmed" subtitle="Your NirnAIn account is ready." features={false}>
        <div className="alert ok">Sign in with your email and password to open your workspace on this device.</div>
        <Link className="btn block lg" to="/login">
          Sign in
        </Link>
      </AuthLayout>
    )
  }

  if (!error) return <FullPageSpinner label="Signing you in" />

  return (
    <AuthLayout
      id="v-callback"
      title="We couldn't sign you in"
      features={false}
      footer={<Link to="/forgot-password">Need a new password reset link?</Link>}
    >
      <div className="alert bad" role="alert">
        {error}
      </div>
      <Link className="btn block lg" to="/login">
        Back to sign in
      </Link>
    </AuthLayout>
  )
}
