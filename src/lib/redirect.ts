/**
 * Safe post-sign-in redirects: the `?next=` check and its trip through OAuth.
 *
 * Purpose: A `next` parameter must never send a user to another site (open redirect), and it has
 * to survive the OAuth round trip without changing the provider's exact redirect URL.
 * How it fits: Used by the auth screens, `auth/guards.tsx`, `auth/OAuthButtons.tsx` and
 * `screens/AuthCallback.tsx`; layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

const AUTH_PATHS = ['/login', '/signup', '/check-email', '/forgot-password', '/auth/callback']

/**
 * Returns `raw` only if it is a same-origin relative path (prevents open redirects
 * via `?next=https://evil.example` or `//evil.example`); otherwise `fallback`.
 */
export function safeNext(raw: string | null | undefined, fallback = '/dashboard'): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return fallback
  try {
    const url = new URL(raw, window.location.origin)
    if (url.origin !== window.location.origin) return fallback
    if (url.pathname === '/' || AUTH_PATHS.includes(url.pathname)) return fallback
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return fallback
  }
}

/** `next` survives the OAuth round-trip here, so the provider redirect URL stays exact. */
const NEXT_STORAGE_KEY = 'projectai:auth-next'

/** Keeps `next` in session storage for the OAuth round trip (the dashboard default is not stored). */
export function rememberNext(next: string): void {
  try {
    if (next && next !== '/dashboard') sessionStorage.setItem(NEXT_STORAGE_KEY, next)
    else sessionStorage.removeItem(NEXT_STORAGE_KEY)
  } catch {
    // Storage can be unavailable (private mode); losing `next` is harmless.
  }
}

/** Reads and clears the `next` kept for the OAuth round trip. */
export function takeRememberedNext(): string | null {
  try {
    const value = sessionStorage.getItem(NEXT_STORAGE_KEY)
    sessionStorage.removeItem(NEXT_STORAGE_KEY)
    return value
  } catch {
    return null
  }
}

/** The `/auth/callback` URL Supabase sends the user back to, carrying `next` when given. */
export function authRedirectUrl(next?: string): string {
  const base = `${window.location.origin}/auth/callback`
  return next ? `${base}?next=${encodeURIComponent(next)}` : base
}
