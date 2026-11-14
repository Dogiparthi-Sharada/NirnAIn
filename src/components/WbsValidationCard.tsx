/**
 * AI validation card of the WBS Builder (mockup views/wbs.js `#wbs-issues`): the result of the
 * `wbs_validate` AI job — deterministic structure checks always, plus Claude's semantic checks when
 * the server has a key. Issues are suggestions only; nothing is added to the WBS until the PM adds
 * it. Hidden until a validation has been requested.
 */
import type { AiJob, WbsIssueType, WbsValidation } from '@/lib/types'

const ISSUE_STYLE: Record<WbsIssueType, { label: string; background: string }> = {
  missing: { label: 'Missing', background: '#F5E3C8' },
  duplicate: { label: 'Duplicate', background: '#F5E3C8' },
  warning: { label: 'Warning', background: '#F3D9D5' },
}

/** Running, failed or finished validation; renders nothing before the first run. */
export default function WbsValidationCard({ job, starting }: { job: AiJob | null | undefined; starting: boolean }) {
  const running = starting || job?.status === 'queued' || job?.status === 'running'
  if (!job && !starting) return null

  if (running) {
    return (
      <div className="card" id="wbs-issues" role="status">
        <h3>AI validation</h3>
        <p className="small muted" style={{ margin: 0 }}>
          Checking structure, coverage and staffing…
        </p>
      </div>
    )
  }
  if (job?.status !== 'succeeded') {
    return (
      <div className="card" id="wbs-issues" role="alert">
        <h3>AI validation</h3>
        <p className="small" style={{ margin: 0, color: 'var(--red)' }}>
          {job?.error ?? 'The validation did not finish. Run AI Validate again.'}
        </p>
      </div>
    )
  }

  const result = job.result as WbsValidation | null
  const issues = result?.issues ?? []
  return (
    <div className="card" id="wbs-issues">
      <h3>
        AI validation: {issues.length === 0 ? 'no issues' : `${issues.length} issue${issues.length > 1 ? 's' : ''}`}
        {result && (
          <span className="small muted" style={{ fontWeight: 400 }}>
            {result.structureValid ? `Structure is valid (${result.levels} levels)` : `Structure needs attention (${result.levels} levels)`}
          </span>
        )}
      </h3>
      {issues.map((issue, index) => (
        <div key={`${issue.type}-${index}`} className="issue" style={{ background: ISSUE_STYLE[issue.type].background }}>
          <b style={{ minWidth: 72 }}>{ISSUE_STYLE[issue.type].label}</b>
          <span>{issue.text}</span>
        </div>
      ))}
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        Suggestions only. Nothing is added to the WBS until you add it.
      </p>
    </div>
  )
}
