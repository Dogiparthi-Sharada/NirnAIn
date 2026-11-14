/**
 * AI validation card of the WBS Builder (mockup views/wbs.js `#wbs-issues`), in two parts:
 * - Built-in checks (`GET …/wbs/checks`): structure, durations, owners, deadline and skill gaps on
 *   the WBS as it is now. They are free, so they re-run after every edit (`lib/wbsChecks.ts`).
 * - AI review (the last `wbs_validate` job): the missing / duplicate work and other items the AI
 *   found, under the time it ran. It costs tokens, so it re-runs only when the PM asks; once the
 *   WBS changed since, a note says so and offers "Re-check (uses AI)".
 * Info lines (e.g. "Skills this WBS needs: …" before the team is set on step 5) use the neutral info
 * style and are not counted in the header. Issues are suggestions only; nothing is added to the WBS
 * until the PM adds it. Hidden while the project has no WBS (the builder's empty state explains that).
 */
import type { ReactNode } from 'react'
import { errorMessage } from '@/lib/api'
import type { AiJob, WbsIssue, WbsIssueType } from '@/lib/types'
import { isCountedIssue, reviewIsStale, reviewIssues, reviewTime, useWbsChecks } from '@/lib/wbsChecks'

const ISSUE_STYLE: Record<WbsIssueType, { label: string; background: string }> = {
  missing: { label: 'Missing', background: '#F5E3C8' },
  duplicate: { label: 'Duplicate', background: '#F5E3C8' },
  warning: { label: 'Warning', background: '#F3D9D5' },
  info: { label: 'Info', background: '#EAF0E2' },
}
const ACTIVE_JOB = new Set(['queued', 'running'])

function SubHeading({ children }: { children: ReactNode }) {
  return (
    <h4 className="small" style={{ margin: '12px 0 0', fontWeight: 600, color: 'var(--secfg)' }}>
      {children}
    </h4>
  )
}

function IssueRows({ issues }: { issues: WbsIssue[] }) {
  return issues.map((issue, index) => (
    <div key={`${issue.type}-${index}-${issue.text}`} className="issue" style={{ background: ISSUE_STYLE[issue.type].background }}>
      <b style={{ minWidth: 72 }}>{ISSUE_STYLE[issue.type].label}</b>
      <span>{issue.text}</span>
    </div>
  ))
}

function Note({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'ok' | 'bad' }) {
  const color = tone === 'ok' ? 'var(--green)' : tone === 'bad' ? 'var(--red)' : 'var(--mfg)'
  return (
    <p className="small" style={{ margin: '6px 0 0', color }}>
      {children}
    </p>
  )
}

/** The built-in checks as of now: loading, error with a retry, or the issues ("No structural issues" when none), then the info lines. */
function LiveChecks({ checks }: { checks: ReturnType<typeof useWbsChecks> }) {
  const live = checks.isError ? undefined : checks.data
  let body: ReactNode
  if (checks.isError) {
    body = (
      <Note tone="bad">
        <span role="alert">The built-in checks couldn&rsquo;t run: {errorMessage(checks.error)}</span>{' '}
        <button className="linkbtn" type="button" onClick={() => void checks.refetch()}>
          Try again
        </button>
      </Note>
    )
  } else if (!live) {
    body = <Note>Running the built-in checks…</Note>
  } else {
    const counted = live.issues.filter(isCountedIssue)
    body = (
      <>
        {counted.length === 0 ? <Note tone="ok">&#10003; No structural issues</Note> : <IssueRows issues={counted} />}
        <IssueRows issues={live.issues.filter((issue) => !isCountedIssue(issue))} />
      </>
    )
  }
  return (
    <section aria-label="Built-in checks" aria-live="polite" aria-busy={checks.isFetching || undefined}>
      <SubHeading>Built-in checks · {checks.isFetching ? 'updating…' : checks.isError ? 'not available' : 'up to date'}</SubHeading>
      {body}
    </section>
  )
}

/** The last AI review: none yet, running, failed, or its own items with its time and the stale note. */
function AiReview({ job, starting, liveAvailable, wbsChangedAt, onRecheck }: {
  job: AiJob | null | undefined
  starting: boolean
  liveAvailable: boolean
  wbsChangedAt: string | null | undefined
  onRecheck: () => void
}) {
  const recheck = (
    <button className="btn sec sm" type="button" onClick={onRecheck} disabled={starting}>
      Re-check (uses AI)
    </button>
  )
  if (starting || ACTIVE_JOB.has(job?.status ?? '')) {
    return (
      <section aria-label="AI review">
        <SubHeading>AI review</SubHeading>
        <p className="small muted" role="status" style={{ margin: '6px 0 0' }}>
          Checking for missing and duplicate work with AI…
        </p>
      </section>
    )
  }
  if (!job) {
    return (
      <section aria-label="AI review">
        <SubHeading>AI review</SubHeading>
        <Note>Not run yet. AI Validate looks for missing and duplicate work (uses AI).</Note>
      </section>
    )
  }
  if (job.status !== 'succeeded') {
    return (
      <section aria-label="AI review">
        <SubHeading>AI review</SubHeading>
        <div className="row between" style={{ marginTop: 6 }}>
          <span className="small" role="alert" style={{ color: 'var(--red)' }}>
            {job.error ?? 'The AI review did not finish.'}
          </span>
          {recheck}
        </div>
      </section>
    )
  }
  const issues = reviewIssues(job, liveAvailable)
  return (
    <section aria-label="AI review">
      <SubHeading>AI review · checked at {reviewTime(job.finishedAt)}</SubHeading>
      {reviewIsStale(job, wbsChangedAt) && (
        <div className="issue" id="wbs-review-stale" style={{ background: '#FBF0DD', color: '#5C3D0E', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>The WBS changed since this AI review.</span>
          {recheck}
        </div>
      )}
      {issues.length > 0 ? <IssueRows issues={issues} /> : <Note>The AI review found nothing else to fix.</Note>}
    </section>
  )
}

/** The card: live built-in checks, then the last AI review. Renders nothing while there is no WBS. */
export default function WbsValidationCard({ projectId, hasWbs, job, starting, onRecheck }: {
  projectId: string
  /** The project has WBS nodes: the checks run and the card shows. */
  hasWbs: boolean
  /** The latest `wbs_validate` job (null: none yet; undefined: loading). */
  job: AiJob | null | undefined
  /** An AI review is being requested. */
  starting: boolean
  /** Runs the AI review again (costs tokens). */
  onRecheck: () => void
}) {
  const checks = useWbsChecks(projectId, hasWbs)
  if (!hasWbs) return null

  const live = checks.isError ? undefined : checks.data
  const total = live ? [...live.issues, ...reviewIssues(job, true)].filter(isCountedIssue).length : null
  return (
    <div className="card" id="wbs-issues">
      <h3 style={{ marginBottom: 0 }}>AI validation{total === null ? '' : total === 0 ? ': no issues' : `: ${total} issue${total > 1 ? 's' : ''}`}</h3>
      <LiveChecks checks={checks} />
      <AiReview job={job} starting={starting} liveAvailable={!checks.isError} wbsChangedAt={live?.wbsChangedAt} onRecheck={onRecheck} />
      <p className="small muted" style={{ margin: '10px 0 0' }}>
        Suggestions only. Nothing is added to the WBS until you add it.
      </p>
    </div>
  )
}
