/**
 * Before/after Gantt of step 7: the new plan over the plan in force, by deliverable.
 *
 * Purpose: The in-force plan is drawn dashed underneath and moved bars use the lighter red, with
 * the deadline and today marked, so the PM sees what a change moves; the axis fits the plans
 * instead of the mockup's fixed 40 weeks.
 * How it fits: Port of the mockup's `gantt(rows, {ghost, deadline, today})`; rendered by
 * `PlanComparison.tsx` from the compare rows; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import type { CompareRow } from '@/lib/changes'
import { DAY_MS, day, monthName, shortDay } from './ui'

interface Span {
  start: number
  end: number
}

interface GanttRow {
  code: string
  name: string
  before: Span | null
  after: Span | null
}

const MAX_MONTH_LABELS = 12

/** Stretches a span to cover `start`–`end` (or starts one). */
function widen(span: Span | null, start: number, end: number): Span {
  return span ? { start: Math.min(span.start, start), end: Math.max(span.end, end) } : { start, end }
}

/** One row per deliverable (level-2 code), spanning its work packages in each plan. */
function groupRows(rows: CompareRow[]): GanttRow[] {
  const groups = new Map<string, GanttRow>()
  for (const row of rows) {
    const key = row.groupCode ?? row.code
    const group = groups.get(key) ?? { code: key, name: row.groupName ?? row.name, before: null, after: null }
    if (row.before) group.before = widen(group.before, day(row.before.start), day(row.before.end))
    if (row.after) group.after = widen(group.after, day(row.after.start), day(row.after.end))
    groups.set(key, group)
  }
  return [...groups.values()]
}

/** Month starts strictly inside the window, thinned to at most 12 labels. */
function monthTicks(min: number, max: number): { label: string; at: number }[] {
  const ticks: { label: string; at: number }[] = []
  const cursor = new Date(min)
  cursor.setUTCDate(1)
  cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  while (cursor.getTime() < max) {
    ticks.push({ label: monthName.format(cursor), at: cursor.getTime() })
    cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  }
  const step = Math.max(1, Math.ceil(ticks.length / MAX_MONTH_LABELS))
  return ticks.filter((_, index) => index % step === 0)
}

/** Plan vN+1 (solid) over Plan vN (dashed); `deadline` is the new plan's must-finish-by. */
export function PlanGantt({ rows, deadline }: { rows: CompareRow[]; deadline: string | null }) {
  const grouped = groupRows(rows)
  const spans = grouped.flatMap((group) => [group.before, group.after]).filter((span): span is Span => span !== null)
  if (spans.length === 0) return <p className="small muted">No scheduled work to show.</p>

  const deadlineDay = deadline ? day(deadline) : null
  const today = day(new Date().toISOString())
  const min = Math.min(...spans.map((span) => span.start))
  const max = Math.max(...spans.map((span) => span.end + DAY_MS), deadlineDay ?? 0)
  const total = Math.max(max - min, DAY_MS)
  /** The horizontal position of a moment on the axis, as a CSS percentage. */
  const left = (time: number) => `${(((time - min) / total) * 100).toFixed(2)}%`
  /** The width of a span on the axis (inclusive of its last day), as a CSS percentage. */
  const width = (span: Span) => `${(((span.end - span.start + DAY_MS) / total) * 100).toFixed(2)}%`
  /** True when a moment falls within the axis. */
  const inside = (time: number | null): time is number => time !== null && time >= min && time <= max

  return (
    <div className="gantt" style={{ paddingBottom: 18 }} role="img" aria-label="Timeline of the new plan against the plan in force">
      <div className="g-row g-axis">
        <div className="g-lbl" />
        <div className="g-track">
          {monthTicks(min, max).map((tick) => (
            <span key={tick.at} style={{ left: left(tick.at) }}>
              {tick.label}
            </span>
          ))}
        </div>
      </div>
      {grouped.map((group) => {
        const moved = group.before && group.after && (group.before.start !== group.after.start || group.before.end !== group.after.end)
        return (
          <div className="g-row" key={group.code}>
            <div className="g-lbl" title={`${group.code} ${group.name}`}>
              <span className="mono">{group.code}</span> {group.name}
            </div>
            <div className="g-track">
              {group.before && <div className="g-bar ghost" style={{ left: left(group.before.start), width: width(group.before) }} />}
              {group.after && <div className={`g-bar${moved ? ' moved' : ''}`} style={{ left: left(group.after.start), width: width(group.after) }} />}
            </div>
          </div>
        )
      })}
      <div className="g-over">
        {inside(deadlineDay) && (
          <div className="g-line" style={{ left: left(deadlineDay) }}>
            <span>Deadline {shortDay(deadlineDay)}</span>
          </div>
        )}
        {inside(today) && (
          <div className="g-line td" style={{ left: left(today) }}>
            <span>Today &middot; {shortDay(today)}</span>
          </div>
        )}
      </div>
    </div>
  )
}
