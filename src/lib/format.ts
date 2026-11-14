const moneyFormatters = new Map<string, Intl.NumberFormat>()

function moneyFormatter(currency: string, compact: boolean): Intl.NumberFormat {
  const key = `${currency}|${compact}`
  let formatter = moneyFormatters.get(key)
  if (!formatter) {
    try {
      formatter = new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        notation: compact ? 'compact' : 'standard',
        maximumFractionDigits: compact ? 1 : 0,
      })
    } catch {
      // Unknown currency code from the server — fall back to USD rather than crash.
      formatter = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
    }
    moneyFormatters.set(key, formatter)
  }
  return formatter
}

/** Formats integer cents as currency, e.g. `24000000` → `$240,000` (or `$240K` when compact). */
/** Formats cents as currency; drafts without a budget render as an em dash. */
export function formatMoney(cents: number | null | undefined, currency = 'USD', options: { compact?: boolean } = {}): string {
  if (cents == null) return '—'
  return moneyFormatter(currency, options.compact ?? false).format(cents / 100)
}

/** Calendar days (`YYYY-MM-DD`) carry no time zone, so they are formatted as written. */
const calendarDayFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})

/** Moments (full ISO timestamps) are shown on the user's own calendar. */
const localDayFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

const CALENDAR_DAY = /^\d{4}-\d{2}-\d{2}$/

/**
 * Formats an ISO date or timestamp as "Nov 13, 2026". Date-only values (`YYYY-MM-DD`) are calendar
 * dates and keep their day in every time zone. Timestamps are shown in the user's local time zone,
 * so a plan applied at 7 pm PST on Nov 13 (03:00 UTC on Nov 14) reads Nov 13.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const calendarDay = CALENDAR_DAY.test(iso)
  const date = new Date(calendarDay ? `${iso}T00:00:00Z` : iso)
  if (Number.isNaN(date.getTime())) return '—'
  return (calendarDay ? calendarDayFormatter : localDayFormatter).format(date)
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : dateTimeFormatter.format(date)
}

const numberFormatter = new Intl.NumberFormat()
export function formatNumber(value: number): string {
  return numberFormatter.format(value)
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : ''
  return (first + last).toUpperCase()
}

/** Today's date as `YYYY-MM-DD` in the user's local time zone (for `<input type="date">`). */
export function todayIsoDate(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}
