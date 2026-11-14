/**
 * Shared bits of the Changes & Versions screen: the animated step list and the mockup's formatting.
 *
 * Purpose: Keeps step 7's wording consistent: weeks from working days (1 week = 5), short date
 * ranges in UTC, and each change's value in the unit of its kind.
 * How it fits: Used by `screens/ChangesVersions.tsx` and `screens/changes/*`; re-exports the
 * provenance `Chip` from `components/InputsKit.tsx`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useState } from 'react'
import { formatDate, formatMoney } from '@/lib/format'
import type { DetectedChange, NewMember } from '@/lib/changes'
import { DAYS_PER_WEEK } from '@/lib/wbs'

export { Chip } from '@/components/InputsKit'
export const DAY_MS = 86_400_000

/** Advances through `count` steps every 700 ms while `active` (purely visual progress for slow calls). */
export function useSteps(active: boolean, count: number): number {
  const [step, setStep] = useState(0)
  useEffect(() => {
    if (!active) {
      setStep(0)
      return
    }
    const timer = window.setInterval(() => setStep((current) => Math.min(current + 1, count - 1)), 700)
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

/** A timestamp as a short UTC date ("Mar 10"). */
export function shortDay(timestamp: number): string {
  return shortDate.format(timestamp)
}

/** "Mar 10–Apr 4" for a plan bar. */
export function range(bar: { start: string; end: string } | null): string {
  if (!bar) return '—'
  return `${shortDate.format(day(bar.start))}–${shortDate.format(day(bar.end))}`
}

/** The value when it is a number, else null. */
function numberOrNull(value: number | string | null): number | null {
  return typeof value === 'number' ? value : null
}

/** A change's before/after value in the unit of its kind (cents, ISO date, working days, hours). */
export function changeValue(change: DetectedChange, value: number | string | null): string {
  switch (change.kind) {
    case 'budget':
      return formatMoney(numberOrNull(value))
    case 'deadline':
      return typeof value === 'string' ? formatDate(value) : '—'
    case 'duration':
      return weeks(numberOrNull(value))
    case 'capacity':
    case 'team':
      return numberOrNull(value) === 0 ? 'off the project' : value == null ? '—' : `${value}h/week`
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
      const from = numberOrNull(change.from)
      const to = numberOrNull(change.to)
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
      return change.member ? `Add ${memberSummary(change.member, numberOrNull(change.to))}` : `Add ${label}`
    default:
      return typeof change.to === 'string' ? change.to : 'Scope change'
  }
}
