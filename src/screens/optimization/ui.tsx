/**
 * Small building blocks of the Optimization Results states: provenance chips and the error and loading notes.
 *
 * Purpose: Keeps the states in the approved design's classes (`.chip`, `.alert`) with one wording
 * for where an input came from.
 * How it fits: Used by `screens/OptimizationResults.tsx` and `screens/optimization/*`;
 * layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import type { ReactNode } from 'react'
import { errorMessage } from '@/lib/api'
import type { InputSource } from '@/lib/optimize'

const SOURCE_CHIP: Record<InputSource, { className: string; label: string }> = {
  stated: { className: 'st-stated', label: 'Stated' },
  confirmed: { className: 'st-confirmed', label: '\u2713 Confirmed' },
  needs_you: { className: 'st-missing', label: 'Needs you' },
  default: { className: 'st-default', label: 'Default' },
  assumed: { className: 'st-assumed', label: 'Assumed' },
  yours: { className: 'st-yours', label: 'Yours' },
}

/** Where a solver input came from, as the coloured chip the design uses. */
export function SourceChip({ source }: { source: InputSource }) {
  const chip = SOURCE_CHIP[source] ?? SOURCE_CHIP.default
  return <span className={`chip ${chip.className}`}>{chip.label}</span>
}

/** Red alert for a failed request, with an optional retry. */
export function ProblemAlert({ title, error, onRetry }: { title: string; error: unknown; onRetry?: () => void }) {
  return (
    <div className="alert bad row between" role="alert">
      <span>
        <b>{title}.</b> {errorMessage(error)}
      </span>
      {onRetry && (
        <button className="btn sec sm" type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

/** Muted one-line note for loading and empty states. */
export function QuietNote({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <p className="small muted" role={busy ? 'status' : undefined} aria-live={busy ? 'polite' : undefined} style={{ margin: '8px 0' }}>
      {children}
    </p>
  )
}
