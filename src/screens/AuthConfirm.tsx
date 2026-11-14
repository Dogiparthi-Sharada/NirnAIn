/**
 * `/auth/confirm`: where links in auth emails land (sign-up confirmation, password reset, email change, invites).
 *
 * Purpose: The email carries a one-time `token_hash` verified with Supabase `verifyOtp`, so the
 * link works in any browser or device, unlike the PKCE `?code=` flow of `/auth/callback`. Recovery
 * links continue to `/reset-password`, others to `?next=` (validated by `safeNext`).
 * How it fits: Route in `App.tsx`; framed by `auth/AuthLayout.tsx`; layer: screen.
 * Created: 2026-10-03 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import type { EmailOtpType } from '@supabase/supabase-js'
import { supabase } from '@/lib/supabase'
import { safeNext } from '@/lib/redirect'
import AuthLayout from '@/auth/AuthLayout'
import { FullPageSpinner } from '@/components/ui'

const EMAIL_OTP_TYPES: readonly EmailOtpType[] = ['signup', 'invite', 'magiclink', 'recovery', 'email_change', 'email']

/** True when the link's `type` is one of the email OTP types Supabase can verify. */
function isEmailOtpType(value: string | null): value is EmailOtpType {
  return value !== null && (EMAIL_OTP_TYPES as readonly string[]).includes(value)
}

/** A spinner while the token is verified, then a redirect or an explanation. */
export default function AuthConfirm() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const params = new URLSearchParams(window.location.search)
    const tokenHash = params.get('token_hash')
    const type = params.get('type')
    if (!tokenHash || !isEmailOtpType(type)) {
      setError('This link is incomplete. Open the most recent email from NirnAIn and try again.')
      return
    }
    const next = type === 'recovery' ? '/reset-password' : safeNext(params.get('next'))

    void supabase.auth.verifyOtp({ token_hash: tokenHash, type }).then(({ error: verifyError }) => {
      if (verifyError) {
        setError(
          'This link has expired or was already used. If you already confirmed your email, you can sign in now — otherwise request a new link.',
        )
        return
      }
      navigate(next, { replace: true })
    })
  }, [navigate])

  if (!error) return <FullPageSpinner label="Confirming your email" />

  return (
    <AuthLayout
      id="v-confirm"
      title="This link didn't work"
      features={false}
      footer={<Link to="/forgot-password">Need a new password reset link?</Link>}
    >
      <div className="alert bad" role="alert">
        {error}
      </div>
      <Link className="btn block lg" to="/login">
        Go to sign in
      </Link>
    </AuthLayout>
  )
}
