/**
 * Sign in (`/login`, mockup views/auth.js `#v-login`): Google / Microsoft OAuth or email + password
 * through Supabase Auth.
 *
 * The email form is checked on the client first (the first problem shows in `#li-err` and its field
 * is focused). Supabase errors are mapped to friendly copy; "email not confirmed" offers to resend
 * the confirmation and "invalid credentials" links to the password reset. On success the auth
 * listener stores the session and `<PublicOnly>` sends the user on to `?next=` (validated by
 * `safeNext`) or the dashboard. Nothing is pre-filled.
 */
import { useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { supabase } from '@/lib/supabase'
import { authRedirectUrl, safeNext } from '@/lib/redirect'
import AuthLayout, { AuthField } from '@/auth/AuthLayout'
import OAuthButtons, { Divider } from '@/auth/OAuthButtons'
import { friendlyAuthError, type FriendlyAuthError } from '@/auth/errors'

type ResendState = 'idle' | 'sending' | 'sent' | 'failed'

const EMAIL_PATTERN = /^\S+@\S+\.\S+$/

/** The sign-in form; keeps the `?next=` target when switching to Create account. */
export default function Login() {
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const nextQuery = next !== '/' ? `?next=${encodeURIComponent(next)}` : ''

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [oauthPending, setOauthPending] = useState(false)
  const [error, setError] = useState<FriendlyAuthError | null>(null)
  const [fieldError, setFieldError] = useState<{ field: 'email' | 'password'; message: string } | null>(null)
  const [resend, setResend] = useState<ResendState>('idle')
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  const busy = submitting || oauthPending

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = email.trim()
    const problem = !trimmed
      ? ({ field: 'email', message: 'Enter your email address.' } as const)
      : !EMAIL_PATTERN.test(trimmed)
        ? ({ field: 'email', message: 'Enter a valid email address.' } as const)
        : !password
          ? ({ field: 'password', message: 'Enter your password.' } as const)
          : null
    setFieldError(problem)
    if (problem) return (problem.field === 'email' ? emailRef : passwordRef).current?.focus()

    setSubmitting(true)
    setError(null)
    setResend('idle')
    const { error: signInError } = await supabase.auth.signInWithPassword({ email: trimmed, password })
    if (signInError) {
      setError(friendlyAuthError(signInError))
      setSubmitting(false)
      passwordRef.current?.select()
    }
    // On success the auth listener updates the session and <PublicOnly> redirects to `next`.
  }

  async function resendConfirmation() {
    setResend('sending')
    const { error: resendError } = await supabase.auth.resend({
      type: 'signup',
      email: email.trim(),
      options: { emailRedirectTo: authRedirectUrl() },
    })
    setResend(resendError ? 'failed' : 'sent')
  }

  return (
    <AuthLayout
      id="v-login"
      title="Sign in"
      subtitle="Access your project workspace"
      footer={
        <>
          New to NirnAIn? <Link to={`/signup${nextQuery}`}>Create an account</Link>
        </>
      }
    >
      <div aria-live="assertive" aria-atomic="true">
        {error && (
          <div className="alert bad">
            {error.message}{' '}
            {error.kind === 'email_not_confirmed' &&
              (resend === 'sent' ? (
                <b>Confirmation email sent to {email.trim()}.</b>
              ) : (
                <button className="linkbtn" type="button" onClick={() => void resendConfirmation()} disabled={resend === 'sending'}>
                  {resend === 'sending' ? 'Sending…' : resend === 'failed' ? "Couldn't send — try again" : 'Resend confirmation email'}
                </button>
              ))}
            {error.kind === 'invalid_credentials' && <Link to="/forgot-password">Reset your password</Link>}
          </div>
        )}
      </div>

      <OAuthButtons
        next={next}
        disabled={submitting}
        onPendingChange={setOauthPending}
        onError={(message) => setError({ kind: 'unknown', message })}
      />

      <Divider label="or sign in with email" />

      <form onSubmit={onSubmit} noValidate>
        <AuthField
          ref={emailRef}
          id="li-email"
          label="Email address"
          type="email"
          name="email"
          autoComplete="username"
          inputMode="email"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="you@company.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          invalid={fieldError?.field === 'email'}
          describedBy={fieldError?.field === 'email' ? 'li-err' : undefined}
          readOnly={busy}
          required
        />
        <AuthField
          ref={passwordRef}
          id="li-pw"
          label="Password"
          aside={
            <Link className="aside" to="/forgot-password">
              Forgot password?
            </Link>
          }
          type="password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          invalid={fieldError?.field === 'password'}
          describedBy={fieldError?.field === 'password' ? 'li-err' : undefined}
          readOnly={busy}
          required
        />
        <p className="err" id="li-err" role="alert" hidden={!fieldError}>
          {fieldError?.message}
        </p>
        <button className="btn block lg" id="btn-signin" type="submit" disabled={busy} aria-busy={submitting || undefined}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </AuthLayout>
  )
}
