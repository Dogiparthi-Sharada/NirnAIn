/**
 * Before/after Gantt of step 7 (port of the mockup's `gantt(rows, {ghost, deadline, today})`).
 *
 * Work packages from GET /changes/{id}/compare are grouped under their deliverable, the
 * in-force plan is drawn dashed underneath, moved bars use the lighter red, and the
 * deadline / today markers are overlaid. The time axis fits the plans instead of the
 * mockup's fixed 40 weeks.
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

function widen(span: Span | null, start: number, end: number): Span {
  return span ? { start: Math.min(span.start, start), end: Math.max(span.end, end) } : { start, end }
}

/** One row per deliverable (level-2 code), spanning its work packages in each plan. */
function groupRows(rows: CompareRow[]): GanttRow[] {
  const groups = new Map<string, GanttRow>()
  for (const r of rows) {
    const key = r.groupCode ?? r.code
    const g = groups.get(key) ?? { code: key, name: r.groupName ?? r.name, before: null, after: null }
    if (r.before) g.before = widen(g.before, day(r.before.start), day(r.before.end))
    if (r.after) g.after = widen(g.after, day(r.after.start), day(r.after.end))
    groups.set(key, g)
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
  return ticks.filter((_, i) => i % step === 0)
}

/** Plan vN+1 (solid) over Plan vN (dashed); `deadline` is the new plan's must-finish-by. */
export function PlanGantt({ rows, deadline }: { rows: CompareRow[]; deadline: string | null }) {
  const grouped = groupRows(rows)
  const spans = grouped.flatMap((g) => [g.before, g.after]).filter((s): s is Span => s !== null)
  if (spans.length === 0) return <p className="small muted">No scheduled work to show.</p>

  const deadlineDay = deadline ? day(deadline) : null
  const today = day(new Date().toISOString())
  const min = Math.min(...spans.map((s) => s.start))
  const max = Math.max(...spans.map((s) => s.end + DAY_MS), deadlineDay ?? 0)
  const total = Math.max(max - min, DAY_MS)
  const left = (t: number) => `${(((t - min) / total) * 100).toFixed(2)}%`
  const width = (s: Span) => `${(((s.end - s.start + DAY_MS) / total) * 100).toFixed(2)}%`
  const inside = (t: number | null): t is number => t !== null && t >= min && t <= max

  return (
    <div className="gantt" style={{ paddingBottom: 18 }} role="img" aria-label="Timeline of the new plan against the plan in force">
      <div className="g-row g-axis">
        <div className="g-lbl" />
        <div className="g-track">
          {monthTicks(min, max).map((m) => (
            <span key={m.at} style={{ left: left(m.at) }}>
              {m.label}
            </span>
          ))}
        </div>
      </div>
      {grouped.map((g) => {
        const moved = g.before && g.after && (g.before.start !== g.after.start || g.before.end !== g.after.end)
        return (
          <div className="g-row" key={g.code}>
            <div className="g-lbl" title={`${g.code} ${g.name}`}>
              <span className="mono">{g.code}</span> {g.name}
            </div>
            <div className="g-track">
              {g.before && <div className="g-bar ghost" style={{ left: left(g.before.start), width: width(g.before) }} />}
              {g.after && <div className={`g-bar${moved ? ' moved' : ''}`} style={{ left: left(g.after.start), width: width(g.after) }} />}
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
