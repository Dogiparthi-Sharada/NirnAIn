/**
 * Choose a new password (`/reset-password`): the end of the reset flow. The emailed link signs the
 * user in through `/auth/callback` or `/auth/confirm` with a recovery session, and this screen
 * sets the new password with Supabase `auth.updateUser`.
 *
 * Without a session the link has expired or was used already; the screen offers a new one. The new
 * password must meet the policy in `auth/password.ts`. Styled like the approved sign-in screens.
 */
import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { supabase } from '@/lib/supabase'
import AuthLayout, { AuthField, PasswordRules } from '@/auth/AuthLayout'
import { useAuth } from '@/auth/useAuth'
import { friendlyAuthError } from '@/auth/errors'
import { passwordMeetsPolicy } from '@/auth/password'
import { FullPageSpinner } from '@/components/ui'

/** New password + confirmation; on success the dashboard opens with a notice. */
export default function ResetPassword() {
  const { session, loading } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ password?: string; confirm?: string }>({})
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const passwordRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)

  if (loading) return <FullPageSpinner label="Verifying your reset link" />

  if (!session) {
    return (
      <AuthLayout
        id="v-reset"
        title="Link expired"
        features={false}
        subtitle="This password reset link is invalid or has expired. Reset links can only be used once."
        footer={<Link to="/login">Back to sign in</Link>}
      >
        <Link className="btn block lg" to="/forgot-password">
          Request a new link
        </Link>
      </AuthLayout>
    )
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors: typeof fieldErrors = {}
    if (!passwordMeetsPolicy(password)) errors.password = 'Your password must meet every requirement below.'
    if (!confirm) errors.confirm = 'Confirm your new password.'
    else if (confirm !== password) errors.confirm = "Passwords don't match."
    setFieldErrors(errors)
    if (errors.password) return passwordRef.current?.focus()
    if (errors.confirm) return confirmRef.current?.focus()

    setSubmitting(true)
    setError(null)
    const { error: updateError } = await supabase.auth.updateUser({ password })
    if (updateError) {
      setError(friendlyAuthError(updateError).message)
      setSubmitting(false)
      return
    }
    navigate('/dashboard', { replace: true, state: { notice: 'Your password has been updated.' } })
  }

  const confirmMismatch = confirm.length > 0 && confirm !== password

  return (
    <AuthLayout
      id="v-reset"
      title="Choose a new password"
      features={false}
      subtitle={
        <>
          Setting a new password for <b>{session.user.email}</b>.
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <div aria-live="assertive" aria-atomic="true">
          {error && <div className="alert bad">{error}</div>}
        </div>
        {/* Lets password managers associate the new password with the right account. */}
        <input type="email" name="email" autoComplete="username" value={session.user.email ?? ''} readOnly hidden />
        <AuthField
          ref={passwordRef}
          id="rp-pw"
          label="New password"
          type="password"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          describedBy="rp-pw-rules"
          readOnly={submitting}
          autoFocus
          required
          hint={<PasswordRules id="rp-pw-rules" password={password} />}
        />
        <AuthField
          ref={confirmRef}
          id="rp-confirm"
          label="Confirm new password"
          type="password"
          name="confirm-password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          error={fieldErrors.confirm ?? (confirmMismatch ? "Passwords don't match." : undefined)}
          readOnly={submitting}
          required
        />
        <button className="btn block lg" type="submit" disabled={submitting} aria-busy={submitting || undefined}>
          {submitting ? 'Updating…' : 'Update password'}
        </button>
      </form>
    </AuthLayout>
  )
}
