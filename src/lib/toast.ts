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

export function subscribeToast(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function currentToast(): string | null {
  return current
}
