/**
 * Check your email (`/check-email`): shown after sign-up when the project requires email confirmation.
 *
 * Purpose: The PM can resend the confirmation email, held back for a 60-second cooldown (also
 * right after sign-up) so it can't hit the provider's rate limit; without an address there is
 * nothing to resend and the button is hidden.
 * How it fits: Port of the mockup's views/auth.js `#v-checkemail`; reached from
 * `screens/Signup.tsx` with the address in router state; calls Supabase `auth.resend`;
 * layer: screen.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { supabase } from '@/lib/supabase'
import { authRedirectUrl } from '@/lib/redirect'
import { stateString } from '@/lib/frame'
import AuthLayout from '@/auth/AuthLayout'
import { friendlyAuthError } from '@/auth/errors'

const RESEND_COOLDOWN_SECONDS = 60

/** Confirmation instructions with a rate-limited resend. */
export default function CheckEmail() {
  const location = useLocation()
  const email = stateString(location.state, 'email') || null
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_SECONDS)
  const [sending, setSending] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = window.setTimeout(() => setCooldown((seconds) => seconds - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])

  /** Resends the confirmation email and starts the cooldown; a failure shows a friendly message. */
  async function resend() {
    if (!email) return
    setSending(true)
    setMessage(null)
    const { error } = await supabase.auth.resend({
      type: 'signup',
      email,
      options: { emailRedirectTo: authRedirectUrl() },
    })
    setSending(false)
    if (error) {
      setMessage({ tone: 'bad', text: friendlyAuthError(error).message })
    } else {
      setMessage({ tone: 'ok', text: 'A new confirmation link is on its way.' })
      setCooldown(RESEND_COOLDOWN_SECONDS)
    }
  }

  return (
    <AuthLayout
      id="v-checkemail"
      title="Check your email"
      features={false}
      subtitle={
        email ? (
          <>
            We sent a confirmation link to <b>{email}</b>. Open it on this device to activate your account.
          </>
        ) : (
          'We sent you a confirmation link. Open it on this device to activate your account.'
        )
      }
      footer={<Link to="/login">Back to sign in</Link>}
    >
      <ul className="small muted" style={{ lineHeight: 1.8, paddingLeft: 18 }}>
        <li>The link expires after 24 hours.</li>
        <li>Can&apos;t find it? Check your spam or promotions folder.</li>
      </ul>

      <div aria-live="polite" aria-atomic="true">
        {message && <div className={`alert ${message.tone}`}>{message.text}</div>}
      </div>

      {email && (
        <button className="btn sec block" type="button" onClick={() => void resend()} disabled={sending || cooldown > 0} aria-busy={sending || undefined}>
          {sending ? 'Sending…' : cooldown > 0 ? `Resend confirmation email in ${cooldown}s` : 'Resend confirmation email'}
        </button>
      )}
    </AuthLayout>
  )
}
