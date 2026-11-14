/**
 * WBS Builder (step 4) validation data: the free built-in checks from `GET …/wbs/checks`, always
 * as of now, and what is left of the last `wbs_validate` AI review. Used by
 * `components/WbsValidationCard.tsx`.
 *
 * Business rules:
 * - The built-in checks (`check_structure` in backend/app/ai/wbs_validate.py) cost nothing, so the
 *   card never shows an old copy: every WBS mutation hook invalidates them (`queries.ts`, `wbs.ts`)
 *   and every visit refetches them.
 * - The AI review costs tokens, so it never re-runs by itself after an edit. Its own items (missing
 *   or duplicate work, …) keep the time it ran; once the WBS changed after the review read it, the
 *   card says so and offers a re-check.
 * - A finding is never shown twice: while the live checks are available, the review's copies of the
 *   built-in checks are dropped.
 * - Step 4 doesn't warn about step 5 (the team comes later): owners are optional and skills are only
 *   named until there is a team. Such lines are 'info' and never counted; the owner and skill
 *   warnings older jobs stored are not shown at all.
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
