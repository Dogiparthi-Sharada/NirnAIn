/**
 * The password policy shown and checked on sign-up and password reset.
 *
 * Purpose: The rules mirror the Supabase Auth password policy configured server-side, so users see
 * every requirement before the server refuses a password.
 * How it fits: Used by `auth/AuthLayout.tsx` (the live checklist) and `screens/Signup.tsx` and
 * `screens/ResetPassword.tsx` (validation); layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

/** Mirrors the Supabase Auth password policy configured server-side. */
export const PASSWORD_RULES = [
  { id: 'length', label: 'At least 10 characters', test: (password: string) => password.length >= 10 },
  { id: 'lower', label: 'One lowercase letter', test: (password: string) => /[a-z]/.test(password) },
  { id: 'upper', label: 'One uppercase letter', test: (password: string) => /[A-Z]/.test(password) },
  { id: 'digit', label: 'One number', test: (password: string) => /\d/.test(password) },
] as const

/** True when the password meets every rule of the policy. */
export function passwordMeetsPolicy(password: string): boolean {
  return PASSWORD_RULES.every((rule) => rule.test(password))
}
