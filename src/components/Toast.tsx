/**
 * The design's `#toast` (styles/base.css `.toast`): a short status message at the bottom of the
 * screen, fed by `toast()` in `lib/toast.ts`. Mounted once at the app root; the live region stays
 * in the DOM so screen readers announce each message.
 */
import { useSyncExternalStore } from 'react'
import { currentToast, subscribeToast } from '@/lib/toast'

/** Renders the latest toast, if any. */
export default function ToastHost() {
  const message = useSyncExternalStore(subscribeToast, currentToast, () => null)
  return (
    <div role="status" aria-live="polite" aria-atomic="true">
      {message && (
        <div className="toast" id="toast">
          {message}
        </div>
      )}
    </div>
  )
}
