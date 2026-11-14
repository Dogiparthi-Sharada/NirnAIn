/**
 * Calendar-date helpers for the results screen (timeline axis, advice wording).
 * Dates on the wire are ISO `YYYY-MM-DD` calendar days; all maths is done in UTC so a
 * negative-offset time zone never shifts a bar or a label by a day.
 */

export const DAY_MS = 86_400_000

const shortDayFormat = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
const monthFormat = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' })

/** Milliseconds since the epoch for an ISO calendar day, at UTC midnight. */
export function dayValue(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`)
}

/** Whole days from `fromIso` to `toIso` (negative when `toIso` is earlier). */
export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((dayValue(toIso) - dayValue(fromIso)) / DAY_MS)
}

/** "Oct 17". */
export function shortDay(iso: string): string {
  return shortDayFormat.format(new Date(dayValue(iso)))
}

/** "Oct" for a UTC timestamp. */
export function monthName(value: number): string {
  return monthFormat.format(new Date(value))
}

/** The Monday on or before a UTC timestamp, so the timeline starts on a week boundary. */
export function weekStart(value: number): number {
  const weekday = new Date(value).getUTCDay()
  const daysSinceMonday = (weekday + 6) % 7
  return value - daysSinceMonday * DAY_MS
}

/** UTC timestamp of the first day of the month after the one containing `value`. */
export function nextMonthStart(value: number): number {
  const date = new Date(value)
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1)
}

/** Number of days from one UTC timestamp to another. */
export function dayCount(fromValue: number, toValue: number): number {
  return Math.round((toValue - fromValue) / DAY_MS)
}

/** "1 day" / "3 days". */
export function pluralDays(count: number): string {
  return `${count} day${count === 1 ? '' : 's'}`
}
