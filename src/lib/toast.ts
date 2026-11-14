/**
 * Toast messages: a tiny external store any code can post a short message to.
 *
 * Purpose: Feedback such as "Signed out." must survive navigation and sign-out, so it lives
 * outside the component tree and shows the latest message for a few seconds.
 * How it fits: `toast()` is called from anywhere; `components/Toast.tsx` (mounted once at the app
 * root) reads it with `useSyncExternalStore`; layer: hook/lib.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

/**
 * Toast messages (the design's `#toast`): call `toast('Signed out.')` from anywhere; `<ToastHost />`
 * (`components/Toast.tsx`, mounted once at the app root so a toast survives navigation and sign-out)
 * shows the latest one for a few seconds. A tiny external store, read with `useSyncExternalStore`.
 */
type Listener = (message: string | null) => void

const listeners = new Set<Listener>()
let current: string | null = null
let hideTimer: number | undefined

const VISIBLE_MS = 3800

/** Sets the current message and tells every listener. */
function emit(message: string | null) {
  current = message
  listeners.forEach((listener) => listener(message))
}

/** Shows `message`, replacing any toast on screen, and hides it after a few seconds. */
export function toast(message: string): void {
  window.clearTimeout(hideTimer)
  emit(message)
  hideTimer = window.setTimeout(() => emit(null), VISIBLE_MS)
}

/** Subscribes to message changes; returns the unsubscribe function. */
export function subscribeToast(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** The message on screen now, or null. */
export function currentToast(): string | null {
  return current
}
