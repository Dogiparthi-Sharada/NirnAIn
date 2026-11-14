/**
 * Timeline by deliverable on Optimization Results (port of the mockup's `gantt()` in
 * views/optimize.js, without the sample week numbers).
 *
 * The run's `result.schedule` lists every work package with its dates; bars here are grouped
 * by deliverable (earliest start to latest finish of its work packages). The axis starts on the
 * Monday of the first week and runs to the later of go-live and the deadline, with month labels
 * placed like the mockup's MONTHS row and a dashed red line at the deadline.
 */
import { formatDate } from '@/lib/format'
import type { ScheduleItem } from '@/lib/optimize'
import { DAY_MS, dayCount, dayValue, monthName, nextMonthStart, shortDay, weekStart } from './dates'

interface DeliverableBar {
  code: string
  name: string
  start: string
  end: string
}

interface AxisLabel {
  text: string
  offsetDays: number
}

/** Month labels closer than this share of the axis are dropped so they never overlap. */
const MIN_LABEL_GAP_PERCENT = 6

/** Orders WBS codes numerically ("1.2" before "1.10"). */
function compareCodes(left: string, right: string): number {
  const leftParts = left.split('.').map(Number)
  const rightParts = right.split('.').map(Number)
  for (let position = 0; position < Math.max(leftParts.length, rightParts.length); position += 1) {
    const difference = (leftParts[position] ?? -1) - (rightParts[position] ?? -1)
    if (difference !== 0 && !Number.isNaN(difference)) return difference
  }
  return left.localeCompare(right)
}

/** One bar per deliverable: from its first work package's start to its last one's finish. */
export function deliverableBars(schedule: ScheduleItem[]): DeliverableBar[] {
  const byCode = new Map<string, DeliverableBar>()
  for (const item of schedule) {
    const existing = byCode.get(item.deliverableCode)
    if (!existing) {
      byCode.set(item.deliverableCode, { code: item.deliverableCode, name: item.deliverableName, start: item.start, end: item.end })
      continue
    }
    // ISO dates compare correctly as strings.
    if (item.start < existing.start) existing.start = item.start
    if (item.end > existing.end) existing.end = item.end
  }
  return [...byCode.values()].sort((left, right) => compareCodes(left.code, right.code))
}

/** Month labels from the axis start to its end, the first one carrying the year. */
function monthLabels(axisStart: number, totalDays: number): AxisLabel[] {
  const labels: AxisLabel[] = []
  const axisEnd = axisStart + totalDays * DAY_MS
  let cursor = axisStart
  let lastPercent = -Infinity
  while (cursor < axisEnd) {
    const offsetDays = dayCount(axisStart, cursor)
    const percent = (offsetDays / totalDays) * 100
    const date = new Date(cursor)
    const withYear = labels.length === 0 || date.getUTCMonth() === 0
    if (percent - lastPercent >= MIN_LABEL_GAP_PERCENT) {
      labels.push({ text: withYear ? `${monthName(cursor)} ${date.getUTCFullYear()}` : monthName(cursor), offsetDays })
      lastPercent = percent
    }
    cursor = nextMonthStart(cursor)
  }
  return labels
}

/** The timeline card body: one row per deliverable, a month axis and the deadline line. */
export function PlanGantt({ schedule, deadline }: { schedule: ScheduleItem[]; deadline: string | null }) {
  const bars = deliverableBars(schedule)
  if (bars.length === 0) {
    return <p className="small muted">This run has no timeline to show.</p>
  }
  const firstStart = Math.min(...bars.map((bar) => dayValue(bar.start)))
  const lastEnd = Math.max(...bars.map((bar) => dayValue(bar.end)), deadline ? dayValue(deadline) : -Infinity)
  const axisStart = weekStart(firstStart)
  // Inclusive end date, padded so the last bar and the deadline label never touch the edge.
  const totalDays = dayCount(axisStart, lastEnd) + 7
  const percentAt = (offsetDays: number) => `${((offsetDays / totalDays) * 100).toFixed(2)}%`
  const deadlineOffset = deadline ? dayCount(axisStart, dayValue(deadline)) : null

  return (
    <div className="gantt" role="group" aria-label="Timeline by deliverable" style={{ paddingBottom: 18 }}>
      <div className="g-row g-axis" aria-hidden="true">
        <div className="g-lbl" />
        <div className="g-track">
          {monthLabels(axisStart, totalDays).map((label) => (
            <span key={label.offsetDays} style={{ left: percentAt(label.offsetDays) }}>
              {label.text}
            </span>
          ))}
        </div>
      </div>
      {bars.map((bar) => {
        const startOffset = dayCount(axisStart, dayValue(bar.start))
        const lengthDays = dayCount(dayValue(bar.start), dayValue(bar.end)) + 1
        const description = `${bar.code} ${bar.name}: ${formatDate(bar.start)} to ${formatDate(bar.end)}`
        return (
          <div className="g-row" key={bar.code}>
            <div className="g-lbl" title={`${bar.code} ${bar.name}`}>
              <span className="mono">{bar.code}</span> {bar.name}
            </div>
            <div className="g-track">
              <div
                className="g-bar"
                role="img"
                aria-label={description}
                title={description}
                style={{ left: percentAt(startOffset), width: percentAt(lengthDays) }}
              />
            </div>
          </div>
        )
      })}
      {deadline && deadlineOffset !== null && (
        <div className="g-over" aria-hidden="true">
          <div className="g-line" style={{ left: percentAt(deadlineOffset + 1) }}>
            <span>Deadline {shortDay(deadline)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
