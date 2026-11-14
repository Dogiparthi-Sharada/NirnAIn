/**
 * Shared bits of the Changes & Versions screen (step 7): provenance chips, the animated
 * step list, and the formatting the mockup uses (weeks, short date ranges).
 *
 * Used by `ChangesVersions.tsx` and the `changes/*` panels. Durations on the wire are
 * working days (1 week = 5); plan dates are ISO calendar dates rendered in UTC.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { formatDate, formatMoney } from '@/lib/format'
import type { DetectedChange, NewMember } from '@/lib/changes'

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
export function Chip({ kind, children }: { kind: ChipKind; children?: ReactNode }) {
  return <span className={`chip ${CHIP[kind][0]}`}>{children ?? CHIP[kind][1]}</span>
}

/** Advances through `count` steps every 700 ms while `active` (purely visual progress for slow calls). */
export function useSteps(active: boolean, count: number): number {
  const [step, setStep] = useState(0)
  useEffect(() => {
    if (!active) {
      setStep(0)
      return
    }
    const timer = window.setInterval(() => setStep((step) => Math.min(step + 1, count - 1)), 700)
    return () => window.clearInterval(timer)
  }, [active, count])
  return step
}

/** The mockup's run box: finished steps ticked, the current one highlighted; announced politely. */
export function RunSteps({ steps, at }: { steps: string[]; at: number }) {
  return (
    <div className="runbox" role="status" aria-live="polite">
      {steps.map((step, index) => (
        <div key={step} className={index < at ? 'done' : index === at ? 'cur' : ''}>
          {step}
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

export function shortDay(timestamp: number): string {
  return shortDate.format(timestamp)
}

/** "Mar 10–Apr 4" for a plan bar. */
export function range(bar: { start: string; end: string } | null): string {
  if (!bar) return '—'
  return `${shortDate.format(day(bar.start))}–${shortDate.format(day(bar.end))}`
}

function num(value: number | string | null): number | null {
  return typeof value === 'number' ? value : null
}

/** A change's before/after value in the unit of its kind (cents, ISO date, working days, hours). */
export function changeValue(change: DetectedChange, value: number | string | null): string {
  switch (change.kind) {
    case 'budget':
      return formatMoney(num(value))
    case 'deadline':
      return typeof value === 'string' ? formatDate(value) : '—'
    case 'duration':
      return weeks(num(value))
    case 'capacity':
    case 'team':
      return num(value) === 0 ? 'off the project' : value == null ? '—' : `${value}h/week`
    default:
      return typeof value === 'string' ? value : '—'
  }
}

/** The small caps label in the left column of a change row. */
export function kindLabel(change: DetectedChange): string {
  if (change.kind === 'duration') return change.to === 0 ? 'Progress' : 'Effort'
  if (change.kind === 'scope') return change.action === 'remove' ? 'Scope' : 'New requirement'
  return { budget: 'Budget', deadline: 'Deadline', capacity: 'Team', team: 'Team' }[change.kind]
}

/** "Dana Lee · Security Specialist · 24 h/wk · contractor at $175/h" (the role only when it isn't the name). */
export function memberSummary(member: NewMember, hours: number | null | undefined = member.hoursPerWeek): string {
  const parts = [member.fullName]
  if (member.roleTitle.toLowerCase() !== member.fullName.toLowerCase()) parts.push(member.roleTitle)
  if (hours != null) parts.push(`${hours} h/wk`)
  if (member.workerType === 'contractor') parts.push(member.hourlyRateCents != null ? `contractor at ${formatMoney(member.hourlyRateCents)}/h` : 'contractor')
  return parts.join(' · ')
}

/** One-line title of a change ("Budget cut", "1.1.1 Salesforce connector: done", …). */
export function changeTitle(change: DetectedChange): string {
  const label = change.targetLabel ?? (change.candidates.length ? 'A work package' : '')
  switch (change.kind) {
    case 'budget': {
      const from = num(change.from)
      const to = num(change.to)
      return from !== null && to !== null && to < from ? 'Budget cut' : 'Budget change'
    }
    case 'deadline':
      return 'Deadline moves'
    case 'duration':
      return change.to === 0 ? `${label}: done` : `${label}: new effort`
    case 'capacity':
      return change.to === 0 ? `${label} leaves the project` : `${label}: hours per week`
    case 'team':
      if (change.action === 'remove') return `Remove ${label}`
      return change.member ? `Add ${memberSummary(change.member, num(change.to))}` : `Add ${label}`
    default:
      return typeof change.to === 'string' ? change.to : 'Scope change'
  }
}
