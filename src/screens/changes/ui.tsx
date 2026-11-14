/**
 * Shared bits of the Changes & Versions screen (step 7): provenance chips, the animated
 * step list, and the formatting the mockup uses (weeks, short date ranges).
 *
 * Used by `ChangesVersions.tsx` and the `changes/*` panels. Durations on the wire are
 * working days (1 week = 5); plan dates are ISO calendar dates rendered in UTC.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { formatDate, formatMoney } from '@/lib/format'
import type { DetectedChange } from '@/lib/changes'

export const DAYS_PER_WEEK = 5
export const DAY_MS = 86_400_000

type ChipKind = 'stated' | 'confirmed' | 'inferred' | 'assumed' | 'missing' | 'default' | 'yours'
const CHIP: Record<ChipKind, [string, string]> = {
  stated: ['st-stated', 'Stated'],
  confirmed: ['st-confirmed', '✓ Confirmed'],
  inferred: ['st-inferred', 'Inferred'],
  assumed: ['st-assumed', 'Assumed'],
  missing: ['st-missing', 'Not found'],
  default: ['st-default', 'Default'],
  yours: ['st-yours', 'Yours'],
}

/** Provenance chip from the approved design (`chip()` in the mockup); `children` overrides the label. */
export function Chip({ k, children }: { k: ChipKind; children?: ReactNode }) {
  return <span className={`chip ${CHIP[k][0]}`}>{children ?? CHIP[k][1]}</span>
}

/** Advances through `count` steps every 700 ms while `active` (purely visual progress for slow calls). */
export function useSteps(active: boolean, count: number): number {
  const [step, setStep] = useState(0)
  useEffect(() => {
    if (!active) {
      setStep(0)
      return
    }
    const t = window.setInterval(() => setStep((s) => Math.min(s + 1, count - 1)), 700)
    return () => window.clearInterval(t)
  }, [active, count])
  return step
}

/** The mockup's run box: finished steps ticked, the current one highlighted; announced politely. */
export function RunSteps({ steps, at }: { steps: string[]; at: number }) {
  return (
    <div className="runbox" role="status" aria-live="polite">
      {steps.map((s, i) => (
        <div key={s} className={i < at ? 'done' : i === at ? 'cur' : ''}>
          {s}
        </div>
      ))}
    </div>
  )
}

/** Working days as the mockup shows them: "4w", or "7d" when not whole weeks. */
export function weeks(days: number | null | undefined): string {
  if (days == null) return '—'
  return days % DAYS_PER_WEEK === 0 ? `${days / DAYS_PER_WEEK}w` : `${days}d`
}

/** A calendar shift in days as "+2w" / "−3d". */
export function signedWeeks(days: number): string {
  const sign = days > 0 ? '+' : days < 0 ? '−' : ''
  const abs = Math.abs(days)
  return abs % 7 === 0 ? `${sign}${abs / 7}w` : `${sign}${abs}d`
}

const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
export const monthName = new Intl.DateTimeFormat(undefined, { month: 'short', timeZone: 'UTC' })

/** Midnight UTC of an ISO date (or datetime) in milliseconds. */
export function day(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`)
}

export function shortDay(ms: number): string {
  return shortDate.format(ms)
}

/** "Mar 10–Apr 4" for a plan bar. */
export function range(bar: { start: string; end: string } | null): string {
  if (!bar) return '—'
  return `${shortDate.format(day(bar.start))}–${shortDate.format(day(bar.end))}`
}

function num(v: number | string | null): number | null {
  return typeof v === 'number' ? v : null
}

/** A change's before/after value in the unit of its kind (cents, ISO date, working days, hours). */
export function changeValue(c: DetectedChange, v: number | string | null): string {
  switch (c.kind) {
    case 'budget':
      return formatMoney(num(v))
    case 'deadline':
      return typeof v === 'string' ? formatDate(v) : '—'
    case 'duration':
      return weeks(num(v))
    case 'capacity':
      return num(v) === 0 ? 'off the project' : v == null ? '—' : `${v}h/week`
    default:
      return typeof v === 'string' ? v : '—'
  }
}

/** The small caps label in the left column of a change row. */
export function kindLabel(c: DetectedChange): string {
  if (c.kind === 'duration') return c.to === 0 ? 'Progress' : 'Effort'
  return { budget: 'Budget', deadline: 'Deadline', capacity: 'Team', scope: 'New requirement' }[c.kind]
}

/** One-line title of a change ("Budget cut", "1.1.1 Salesforce connector: done", …). */
export function changeTitle(c: DetectedChange): string {
  const label = c.targetLabel ?? (c.candidates.length ? 'A work package' : '')
  switch (c.kind) {
    case 'budget': {
      const from = num(c.from)
      const to = num(c.to)
      return from !== null && to !== null && to < from ? 'Budget cut' : 'Budget change'
    }
    case 'deadline':
      return 'Deadline moves'
    case 'duration':
      return c.to === 0 ? `${label}: done` : `${label}: new effort`
    case 'capacity':
      return c.to === 0 ? `${label} leaves the project` : `${label}: hours per week`
    default:
      return typeof c.to === 'string' ? c.to : 'Scope change'
  }
}
