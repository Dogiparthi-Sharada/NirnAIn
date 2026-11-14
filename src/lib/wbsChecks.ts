/**
 * WBS Builder validation data: the free built-in checks and what is left of the last AI review.
 *
 * Purpose: The built-in checks cost nothing, so every WBS change invalidates them and the card
 * never shows an old copy; the AI review costs tokens, so it never re-runs by itself and its
 * findings keep the time they ran. A finding is never shown twice, and step 5 matters (owners,
 * skills before there is a team) are info only.
 * How it fits: Reads `GET …/wbs/checks` and the latest `wbs_validate` job for
 * `components/WbsValidationCard.tsx`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'
import { queryKeys } from './queries'
import type { AiJob, WbsChecks, WbsIssue, WbsValidation } from './types'

/**
 * How the built-in checks word their findings, for validate jobs stored before each issue carried a
 * `source` (backend `wbs_validate.py` at that time). Newer jobs tag every issue.
 */
const UNTAGGED_RULE_MESSAGES = [
  /^The WBS is empty\./,
  / has no (deliverables|work packages|duration|owner)\.$/,
  /^\d+ work packages have no (duration|owner) \(/,
  /^Duplicate name "/,
  /^The critical path takes \d+ working days, but /,
  / skill gap: no (team member|active employee) has it\.$/,
]

/** Warnings older jobs gave about step 5 matters (owners, skills against everyone): no longer step-4 issues. */
const RETIRED_RULE_MESSAGES = [
  / has no owner\.$/,
  /^\d+ work packages have no owner \(/,
  / skill gap: no (team member|active employee) has it\.$/,
]

const clockFormatter = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const dayClockFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/** The built-in checks on the current WBS, team and constraints; `enabled` false while there is no WBS. */
export function useWbsChecks(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.wbsChecks(projectId),
    queryFn: ({ signal }) => apiFetch<WbsChecks>(`/api/v1/projects/${encodeURIComponent(projectId)}/wbs/checks`, { signal }),
    enabled: Boolean(projectId) && enabled,
    // Free and fast: every visit asks again, so changes made on other steps show at once.
    staleTime: 0,
  })
}

/** True for a real step-4 issue (counted in the card's header); 'info' lines are notes. */
export function isCountedIssue(issue: WbsIssue): boolean {
  return issue.type !== 'info'
}

/** True for a finding of the built-in checks (rather than the AI review's own). */
export function isRuleIssue(issue: WbsIssue): boolean {
  if (issue.source) return issue.source === 'rules'
  return UNTAGGED_RULE_MESSAGES.some((pattern) => pattern.test(issue.text))
}

/**
 * The AI review's items to list: only its own when the live checks are available (they show the
 * rest as of now), else everything it found, under its time; never the retired step-5 warnings.
 */
export function reviewIssues(job: AiJob | null | undefined, liveAvailable: boolean): WbsIssue[] {
  if (job?.status !== 'succeeded') return []
  const stored = (job.result as WbsValidation | null)?.issues ?? []
  const issues = stored.filter((issue) => !RETIRED_RULE_MESSAGES.some((pattern) => pattern.test(issue.text)))
  return liveAvailable ? issues.filter((issue) => !isRuleIssue(issue)) : issues
}

/**
 * True when the WBS changed after the review read it. The job reads the WBS when it starts, so an
 * edit made while it ran counts too (finishedAt for jobs without a start time).
 */
export function reviewIsStale(job: AiJob | null | undefined, wbsChangedAt: string | null | undefined): boolean {
  const readAt = job?.startedAt ?? job?.finishedAt
  if (job?.status !== 'succeeded' || !readAt || !wbsChangedAt) return false
  return Date.parse(wbsChangedAt) > Date.parse(readAt)
}

/** "1:03 PM" today, "Nov 13, 1:03 PM" on another day (local time); '' when unreadable. */
export function reviewTime(iso: string | null | undefined, now: Date = new Date()): string {
  const time = iso ? new Date(iso) : null
  if (!time || Number.isNaN(time.getTime())) return ''
  return (time.toDateString() === now.toDateString() ? clockFormatter : dayClockFormatter).format(time)
}
