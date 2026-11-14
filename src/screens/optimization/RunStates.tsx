/**
 * The solving and infeasible states of Optimization Results (port of `#opt-solving` and
 * `#opt-infeasible` in the mockup's views/optimize.js).
 *
 * Solving mirrors the run's real status (queued → "Building model", running → "Solving"); the
 * screen polls the run meanwhile. Infeasible lists the constraints that can't all hold
 * (`run.iis`) and the priced remedies the backend computed (`run.result.remedies`); picking one
 * and pressing "Re-solve with this change" starts a new run with that remedy.
 */
import { useState } from 'react'
import { Link } from 'react-router'
import { formatDate, formatMoney } from '@/lib/format'
import type { PlanRun, Remedy, RemedyId } from '@/lib/optimize'
import { stepPath } from '@/lib/steps'
import { ProblemAlert } from './ui'

const SOLVE_STEPS = ['Building model', 'Solving', 'Preparing results']

/** The spinner and the three solver steps, shown while a run is queued or running. */
export function SolvingState({ running, inputsHash }: { running: boolean; inputsHash: string | null }) {
  const currentStep = running ? 1 : 0
  return (
    <div id="opt-solving" className="card center" role="status" aria-live="polite">
      <div className="ring" aria-hidden="true" />
      <div className="runbox" style={{ display: 'inline-block', textAlign: 'left', border: 0 }}>
        {SOLVE_STEPS.map((label, index) => (
          <div key={label} className={index < currentStep ? 'done' : index === currentStep ? 'cur' : ''}>
            {label}
          </div>
        ))}
      </div>
      <p className="small muted">OR-Tools is solving your plan&hellip; This usually takes under a minute. You can leave this page open.</p>
      {inputsHash && <p className="small mono muted">Inputs locked &middot; hash {inputsHash}</p>}
    </div>
  )
}

/** "Go-live Sep 19, 2025 · about $348,600 · <detail>". */
function remedyFacts(remedy: Remedy): string {
  const facts = [
    remedy.completionDate ? `Go-live ${formatDate(remedy.completionDate)}` : null,
    remedy.costCents != null ? `about ${formatMoney(remedy.costCents)}` : null,
    remedy.detail || null,
  ]
  return facts.filter((fact): fact is string => fact !== null).join(' \u00B7 ')
}

interface InfeasibleStateProps {
  projectId: string
  run: PlanRun
  resolving: boolean
  resolveError: unknown
  onResolve: (remedy: RemedyId) => void
}

/** Why the plan can't be built as specified, and what it would take. */
export function InfeasibleState({ projectId, run, resolving, resolveError, onResolve }: InfeasibleStateProps) {
  const [picked, setPicked] = useState<RemedyId | null>(null)
  const conflicts = run.iis ?? []
  const remedies = run.result?.remedies ?? []

  return (
    <div id="opt-infeasible">
      <div className="alert bad" role="alert">
        <b>This plan is infeasible as specified.</b>
        <br />
        No assignment satisfies every hard constraint at once. The constraints below cannot all hold together.
        {conflicts.map((conflict) => (
          <div className="iis" key={conflict.name}>
            <div className="lbl">Conflicting constraint</div>
            <div className="mono" style={{ fontWeight: 600 }}>
              {conflict.name}
            </div>
            <div className="small">{conflict.explanation}</div>
          </div>
        ))}
        {conflicts.length === 0 && <div className="iis small">The solver did not report which constraints conflict.</div>}
        <p style={{ margin: '10px 0 0' }}>
          <Link to={stepPath('constraints', projectId)}>Edit constraints</Link>
        </p>
      </div>

      <div className="card">
        <h3>What it would take</h3>
        {remedies.length === 0 ? (
          <p className="small muted" style={{ margin: 0 }}>
            No single change makes this plan possible. Edit the constraints on step 5 and run again.
          </p>
        ) : (
          <>
            <p className="small muted" id="remedy-help" style={{ margin: '0 0 4px' }}>
              Each option changes as little as possible. Pick one and the solver runs again.
            </p>
            <div role="group" aria-describedby="remedy-help">
              {remedies.map((remedy) => (
                <button
                  key={remedy.id}
                  className={`remedy${picked === remedy.id ? ' picked' : ''}`}
                  data-remedy={remedy.id}
                  type="button"
                  aria-pressed={picked === remedy.id}
                  onClick={() => setPicked(remedy.id)}
                >
                  <span className="radio" aria-hidden="true" />
                  <span>
                    <b>{remedy.title}</b> <span className="chip st-inferred">1 change</span>
                    <br />
                    <span className="small muted">{remedyFacts(remedy)}</span>
                  </span>
                </button>
              ))}
            </div>
            {resolveError != null && (
              <div className="mt8">
                <ProblemAlert title="The solver didn't start" error={resolveError} />
              </div>
            )}
            <div className="row end mt">
              <button
                className="btn"
                id="btn-resolve"
                type="button"
                disabled={picked === null || resolving}
                aria-describedby="remedy-help"
                title={picked === null ? 'Pick one of the options above first.' : undefined}
                onClick={() => picked && onResolve(picked)}
              >
                {resolving ? 'Starting\u2026' : 'Re-solve with this change'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
