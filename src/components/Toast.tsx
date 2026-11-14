/**
 * The app-wide toast: a short status message at the bottom of the screen.
 *
 * Purpose: Confirmations such as "Signed out." must survive navigation and sign-out; the live
 * region stays in the DOM so screen readers announce each message.
 * How it fits: Mounted once at the app root by `App.tsx`; fed by `toast()` in `lib/toast.ts`;
 * styled by the design's `.toast` (styles/base.css); layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
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
