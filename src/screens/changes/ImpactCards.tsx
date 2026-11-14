/**
 * Side cards of the step 7 impact view: what a change touches, what the re-plan will
 * regenerate, and why a re-plan produced no plan. Display only; data comes from the
 * change's `impact` and C1's optimization run.
 */
import type { ChangeRequest, DetectedChange } from '@/lib/changes'
import { formatDate, formatMoney } from '@/lib/format'
import type { OptimizationRun } from '@/lib/types'
import { Chip } from './ui'

/** Work and people the change reaches, and the in-force plan it is measured against. */
export function Touches({ change, baseVersion }: { change: ChangeRequest; baseVersion: number }) {
  const a = change.impact?.affects
  if (!a) return null
  return (
    <div className="card">
      <h3>What it touches</h3>
      <div className="risk">
        <Chip k="default">Work</Chip>
        <div>
          {a.tasks.length ? a.tasks.map((t) => `${t.code} ${t.name}`).join(', ') : 'No work package directly'}
          <small>{a.tasks.length ? 'Changed work packages and everything that waits on them.' : 'Budget and deadline changes re-solve the whole plan.'}</small>
        </div>
      </div>
      <div className="risk">
        <Chip k="default">People</Chip>
        <div>
          {a.people.length ? a.people.map((p) => p.name).join(', ') : 'Nobody directly'}
          <small>Owners of that work, and anyone whose hours change.</small>
        </div>
      </div>
      <div className="risk">
        <Chip k="yours">Plan v{baseVersion}</Chip>
        <div>
          Go-live {formatDate(a.completionDate)} &middot; {formatMoney(a.costCents)}
          <small>What the new plan is measured against. It stays in force until you approve.</small>
        </div>
      </div>
    </div>
  )
}

/** The mockup's "What will be regenerated" tiles, filled from the detected changes. */
export function Regenerated({ changes }: { changes: DetectedChange[] }) {
  const count = (pred: (c: DetectedChange) => boolean) => changes.filter(pred).length
  const budget = count((c) => c.kind === 'budget')
  const deadline = count((c) => c.kind === 'deadline')
  const effort = count((c) => c.kind === 'duration' && c.to !== 0)
  const done = count((c) => c.kind === 'duration' && c.to === 0)
  const people = count((c) => c.kind === 'capacity')
  const scope = count((c) => c.kind === 'scope')
  const limits = [budget ? 'Budget' : '', deadline ? 'deadline' : ''].filter(Boolean).join(' and ')
  const tiles: [string, string][] = [
    ['Constraints', limits ? `${limits}, if you take the new value` : 'Unchanged'],
    ['WBS', [effort ? `${effort} effort change${effort > 1 ? 's' : ''}` : '', done ? `${done} done` : ''].filter(Boolean).join(' · ') || 'Unchanged'],
    ['Team', people ? `${people} ${people > 1 ? 'people’s' : 'person’s'} hours` : 'Unchanged'],
    ['Timeline', 'Re-solved, minimal disruption'],
    ['Assignments & cost', 'Recalculated'],
    ['Scope', scope ? `${scope} new requirement${scope > 1 ? 's' : ''}: add in WBS Builder` : 'Unchanged'],
  ]
  return (
    <div className="art">
      {tiles.map(([k, v]) => (
        <div className="a" key={k}>
          <b>{k}</b>
          <span>{v}</span>
        </div>
      ))}
    </div>
  )
}

/** An infeasible re-plan lists the conflicting constraints; other failures show the run's error. */
export function RunProblem({ run }: { run: OptimizationRun }) {
  if (run.status !== 'infeasible') {
    return (
      <div className="alert bad" role="alert">
        <b>The re-plan didn’t finish.</b> {run.error ?? 'Please try again.'}
      </div>
    )
  }
  return (
    <div className="alert bad" role="alert">
      <b>The new plan is infeasible as specified.</b>
      <br />
      No plan meets every hard constraint with these changes. Pick other answers above (for example keep the current value) and re-plan.
      {(run.iis ?? []).map((c) => (
        <div className="iis" key={c.name}>
          <div className="lbl">Conflicting constraint</div>
          <div className="mono" style={{ fontWeight: 600 }}>
            {c.name}
          </div>
          <div className="small">{c.explanation}</div>
        </div>
      ))}
    </div>
  )
}
