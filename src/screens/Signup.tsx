/**
 * Create account (`/signup`, mockup views/auth.js `#v-signup`): Google / Microsoft OAuth or email
 * sign-up through Supabase Auth.
 *
 * Client checks mirror the server policy (`auth/password.ts`) and focus the first invalid field.
 * The full name goes into the user metadata (`full_name`) for the profile row. When the project
 * requires email confirmation Supabase returns no session and the user goes to `/check-email`;
 * otherwise straight on to `?next=` (validated by `safeNext`) or the dashboard.
 */
import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { supabase } from '@/lib/supabase'
import { authRedirectUrl, safeNext } from '@/lib/redirect'
import AuthLayout, { AuthField, PasswordRules } from '@/auth/AuthLayout'
import OAuthButtons, { Divider } from '@/auth/OAuthButtons'
import { friendlyAuthError } from '@/auth/errors'
import { passwordMeetsPolicy } from '@/auth/password'

interface FieldErrors {
  firstName?: string
  email?: string
  password?: string
  confirm?: string
}

const PANEL = {
  heading: 'Create your planning workspace',
  text: 'Bring in meetings, documents and requirements. NirnAIn structures them into a plan you can review, optimize and approve.',
}

/** The create-account form; keeps the `?next=` target when switching to Sign in. */
export default function Signup() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))

  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [oauthPending, setOauthPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const refs = {
    firstName: useRef<HTMLInputElement>(null),
    email: useRef<HTMLInputElement>(null),
    password: useRef<HTMLInputElement>(null),
    confirm: useRef<HTMLInputElement>(null),
  }

  const busy = submitting || oauthPending
  const confirmMismatch = confirm.length > 0 && confirm !== password

  function validate(): FieldErrors {
    const errors: FieldErrors = {}
    if (!firstName.trim()) errors.firstName = 'Enter your first name.'
    const trimmed = email.trim()
    if (!trimmed) errors.email = 'Enter your work email.'
    else if (!/^\S+@\S+\.\S+$/.test(trimmed)) errors.email = 'Enter a valid email address.'
    if (!passwordMeetsPolicy(password)) errors.password = 'Your password must meet every requirement below.'
    if (!confirm) errors.confirm = 'Confirm your password.'
    else if (confirm !== password) errors.confirm = "Passwords don't match."
    return errors
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors = validate()
    setFieldErrors(errors)
    const firstInvalid = (Object.keys(refs) as (keyof FieldErrors)[]).find((key) => errors[key])
    if (firstInvalid) return refs[firstInvalid].current?.focus()

    setSubmitting(true)
    setError(null)
    const trimmedEmail = email.trim()
    const fullName = [firstName.trim(), lastName.trim()].filter(Boolean).join(' ')
    const { data, error: signUpError } = await supabase.auth.signUp({
      email: trimmedEmail,
      password,
      options: { data: { full_name: fullName }, emailRedirectTo: authRedirectUrl() },
    })
    if (signUpError) {
      setError(friendlyAuthError(signUpError).message)
      setSubmitting(false)
      return
    }
    // With email confirmation disabled Supabase returns a live session; go straight in.
    if (data.session) navigate(next, { replace: true })
    else navigate('/check-email', { replace: true, state: { email: trimmedEmail } })
  }

  return (
    <AuthLayout
      id="v-signup"
      title="Create account"
      subtitle="Start planning in minutes"
      panel={PANEL}
      footer={
        <>
          Already have an account? <Link to={next !== '/' ? `/login?next=${encodeURIComponent(next)}` : '/login'}>Sign in</Link>
        </>
      }
    >
      <div aria-live="assertive" aria-atomic="true">
        {error && <div className="alert bad">{error}</div>}
      </div>

      <OAuthButtons next={next} disabled={submitting} onPendingChange={setOauthPending} onError={setError} />

      <Divider label="or sign up with email" />

      <form onSubmit={onSubmit} noValidate>
        <div className="row" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
          <AuthField
            ref={refs.firstName}
            id="su-first"
            className="min-w-0 flex-1"
            label="First name"
            name="given-name"
            autoComplete="given-name"
            placeholder="Alex"
            value={firstName}
            onChange={(event) => setFirstName(event.target.value)}
            error={fieldErrors.firstName}
            readOnly={busy}
            required
          />
          <AuthField
            id="su-last"
            className="min-w-0 flex-1"
            label="Last name"
            name="family-name"
            autoComplete="family-name"
            placeholder="Morgan"
            value={lastName}
            onChange={(event) => setLastName(event.target.value)}
            readOnly={busy}
          />
        </div>
        <AuthField
          ref={refs.email}
          id="su-email"
          label="Work email"
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="you@company.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldErrors.email}
          readOnly={busy}
          required
        />
        <AuthField
          ref={refs.password}
          id="su-pw"
          label="Password"
          type="password"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          describedBy="su-pw-rules"
          readOnly={busy}
          required
          hint={<PasswordRules id="su-pw-rules" password={password} />}
        />
        <AuthField
          ref={refs.confirm}
          id="su-confirm"
          label="Confirm password"
          type="password"
          name="confirm-password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          error={fieldErrors.confirm ?? (confirmMismatch ? "Passwords don't match." : undefined)}
          readOnly={busy}
          required
        />
        <button className="btn block lg" type="submit" disabled={busy} aria-busy={submitting || undefined}>
          {submitting ? 'Creating account…' : 'Create account'}
        </button>
        <p className="foot small">By creating an account you agree to your organization&apos;s acceptable use policy.</p>
      </form>
    </AuthLayout>
  )
}
