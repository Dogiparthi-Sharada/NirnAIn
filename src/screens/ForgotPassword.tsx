/**
 * Reset your password (`/forgot-password`): asks Supabase to email a reset link.
 *
 * Purpose: Privacy: the same "Reset link requested" confirmation shows whether or not an account
 * exists, so the form can't be used to discover registered emails; only network and rate-limit
 * failures are reported as errors.
 * How it fits: Port of the mockup's views/auth.js `#v-forgot`; the link comes back through
 * `/auth/callback` to `screens/ResetPassword.tsx`; layer: screen.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { supabase } from '@/lib/supabase'
import { authRedirectUrl } from '@/lib/redirect'
import AuthLayout, { AuthField } from '@/auth/AuthLayout'
import { friendlyAuthError } from '@/auth/errors'

/** Email in, reset link out; the confirmation stays above the form so another address can be tried. */
export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)

  /** Checks the email and requests the reset link; only network and rate-limit failures show as errors. */
  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = email.trim()
    if (!/^\S+@\S+\.\S+$/.test(trimmed)) {
      setFieldError(trimmed ? 'Enter a valid email address.' : 'Enter your email address.')
      emailRef.current?.focus()
      return
    }
    setFieldError(null)
    setError(null)
    setSentTo(null)
    setSubmitting(true)
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(trimmed, {
      redirectTo: authRedirectUrl('/reset-password'),
    })
    setSubmitting(false)
    // Only transport/rate-limit failures are surfaced; everything else shows the same
    // confirmation so the form can't be used to discover which emails have accounts.
    const friendly = resetError ? friendlyAuthError(resetError) : null
    if (friendly && (friendly.kind === 'network' || friendly.kind === 'rate_limited')) {
      setError(friendly.message)
      return
    }
    setSentTo(trimmed)
  }

  return (
    <AuthLayout
      id="v-forgot"
      title="Reset your password"
      features={false}
      subtitle="Enter the email you use for NirnAIn and we'll send you a link to choose a new password."
      footer={<Link to="/login">Back to sign in</Link>}
    >
      <div aria-live="polite" aria-atomic="true">
        {sentTo && (
          <div id="fp-done" className="alert ok">
            <b>Reset link requested</b>
            <br />
            If an account exists for {sentTo}, you&apos;ll receive an email with a link to reset your password within a few
            minutes.
          </div>
        )}
        {error && (
          <div className="alert bad" role="alert">
            {error}
          </div>
        )}
      </div>
      <form onSubmit={onSubmit} noValidate>
        <AuthField
          ref={emailRef}
          id="fp-email"
          label="Email address"
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="you@company.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldError}
          readOnly={submitting}
          autoFocus
          required
        />
        <button className="btn block" type="submit" disabled={submitting} aria-busy={submitting || undefined}>
          {submitting ? 'Sending…' : 'Send reset link'}
        </button>
      </form>
    </AuthLayout>
  )
}
