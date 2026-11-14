/**
 * Frame and form pieces of the public sign-in screens (mockup views/auth.js, styles/public.css):
 * the wine brand panel on the left and the form column on the right, plus the design's field,
 * password-rules and brand markup.
 *
 * Used by Login, Signup, CheckEmail, ForgotPassword, ResetPassword, AuthCallback and AuthConfirm.
 * Only presentation lives here; the Supabase calls stay in each screen.
 */
import { useEffect, type InputHTMLAttributes, type ReactNode, type Ref } from 'react'
import { APP_NAME } from '@/components/Brand'
import { PASSWORD_RULES } from './password'
import '@/styles/nirnain/public-product.css'

const FEATURES = [
  'AI extracts scope from briefs, transcripts and RFPs',
  'Validated work breakdown structures in minutes',
  'Optimized schedules and resource allocation',
]

const DEFAULT_PANEL = {
  heading: 'AI-Assisted Project Planning Platform',
  text: 'Plan projects intelligently. AI extracts scope, builds WBS structures, and optimizes resource allocation automatically.',
}

/** The "N" mark and the Nirn<em>AI</em>n wordmark; `inverted` for the wine panel. */
export function BrandLockup({ inverted = false }: { inverted?: boolean }) {
  return (
    <div className="brand">
      <span className={inverted ? 'bmark inv' : 'bmark'} aria-hidden="true">
        N
      </span>
      <span className={inverted ? 'bname inv' : 'bname'}>
        Nirn<em>AI</em>n
      </span>
    </div>
  )
}

/**
 * One sign-in screen: brand panel (heading, text, optional feature list) and the form column with
 * its title, subtitle, content and footer line. Sets the document title.
 */
export default function AuthLayout({
  id,
  title,
  subtitle,
  children,
  footer,
  panel = DEFAULT_PANEL,
  features = true,
}: {
  /** Section id from the design (`v-login`, `v-signup`, …). */
  id: string
  title: string
  subtitle?: ReactNode
  children: ReactNode
  footer?: ReactNode
  /** Copy for the brand panel. */
  panel?: { heading: string; text: string }
  /** Show the three feature ticks under the panel text. */
  features?: boolean
}) {
  useEffect(() => {
    document.title = `${title} · ${APP_NAME}`
  }, [title])

  return (
    <section className="view on auth" id={id}>
      <aside className="auth-panel">
        <BrandLockup inverted />
        <h2>{panel.heading}</h2>
        <p>{panel.text}</p>
        {features && (
          <ul className="feat">
            {FEATURES.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>
        )}
        <div className="copy">
          &copy; {new Date().getFullYear()} {APP_NAME} Inc.
        </div>
      </aside>
      <main className="auth-main">
        <div className="auth-box">
          <h1>{title}</h1>
          {subtitle && <p className="sub muted">{subtitle}</p>}
          {children}
          {footer && <p className="foot">{footer}</p>}
        </div>
      </main>
    </section>
  )
}

interface AuthFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'className'> {
  id: string
  label: string
  /** Right-aligned extra in the label row, e.g. "Forgot password?". */
  aside?: ReactNode
  /** Message under the field; also outlines it red. */
  error?: string | null
  /** Outline red without a message of its own (the screen shows one elsewhere). */
  invalid?: boolean
  /** Ids of elements describing the field (an error shown elsewhere, the password rules). */
  describedBy?: string
  /** Shown under the input, e.g. the password rules. */
  hint?: ReactNode
  className?: string
  ref?: Ref<HTMLInputElement>
}

/** A labelled input in the design's `.field` markup, with an accessible error state. */
export function AuthField({ id, label, aside, error, invalid, describedBy, hint, className, ref, ...inputProps }: AuthFieldProps) {
  const errorId = error ? `${id}-err` : undefined
  const bad = Boolean(error) || invalid
  return (
    <div className={className ? `field ${className}` : 'field'}>
      <label htmlFor={id}>
        {label}
        {aside}
      </label>
      <input
        ref={ref}
        id={id}
        className={bad ? 'in invalid' : 'in'}
        aria-invalid={bad || undefined}
        aria-describedby={[errorId, describedBy].filter(Boolean).join(' ') || undefined}
        {...inputProps}
      />
      {error && (
        <p className="err" id={errorId}>
          {error}
        </p>
      )}
      {hint}
    </div>
  )
}

/** The password policy (`auth/password.ts`) as the design's `.pwlist`; met rules turn green. */
export function PasswordRules({ id, password }: { id: string; password: string }) {
  return (
    <ul className="pwlist" id={id} aria-label="Password requirements">
      {PASSWORD_RULES.map((rule) => {
        const met = rule.test(password)
        return (
          <li key={rule.id} className={met ? 'ok' : undefined}>
            {rule.label}
            {met && <span className="sr-only"> (done)</span>}
          </li>
        )
      })}
    </ul>
  )
}
