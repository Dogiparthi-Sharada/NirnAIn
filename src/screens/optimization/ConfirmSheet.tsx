/**
 * The confirmation sheet of Optimization Results (`#opt-confirm`; port of `renderConfirm()` in the
 * mockup's views/optimize.js): "Check the inputs the solver will use".
 *
 * Nothing runs until the project manager approves this exact set of inputs. Rows come from
 * `GET …/optimization/inputs` with a provenance chip each; `ready: false` disables approval and
 * lists what is blocking, each with a link to the step that fixes it. The Constraint check brief
 * repeats the Must hold / Can flex warnings from Constraints. A failed start (422 with `missing`,
 * 403, 409) is explained here, below the table.
 */
import type { UseQueryResult } from '@tanstack/react-query'
import { Link } from 'react-router'
import { errorMessage, isApiError } from '@/lib/api'
import type { PolicyRisk } from '@/lib/constraintPolicy'
import { missingInputHints, type OptimizationInputs } from '@/lib/optimize'
import { stepPath, useStepNav } from '@/lib/steps'
import { ConstraintCheckBrief } from './ConstraintCheck'
import { ProblemAlert, QuietNote, SourceChip } from './ui'

interface ConfirmSheetProps {
  projectId: string
  inputs: UseQueryResult<OptimizationInputs>
  /** Must hold / Can flex warnings for the current settings. */
  risks: PolicyRisk[]
  /** Which banner to show above the sheet. */
  context: 'first-run' | 'inputs-changed' | 'run-again'
  starting: boolean
  startError: unknown
  onApprove: () => void
  /** Back to the latest run's results (only when there is one). */
  onCancel?: () => void
}

/** The step a blocking reason points to ("… on step 4" → WBS Builder, otherwise Constraints). */
function blockingStep(reason: string): 'wbs' | 'constraints' {
  return /step 4/i.test(reason) ? 'wbs' : 'constraints'
}

/** Why the last attempt to start a run failed, with links when the server named missing inputs. */
function StartProblem({ error, projectId }: { error: unknown; projectId: string }) {
  if (isApiError(error) && error.status === 403) {
    return <div className="alert warn mt">Only project editors can run the optimizer. Ask a project owner or manager for access.</div>
  }
  const hints = isApiError(error) && error.status === 422 ? missingInputHints(error) : []
  if (hints.length === 0) return <div className="mt"><ProblemAlert title="The solver didn't start" error={error} /></div>
  return (
    <div className="cf-banner bad mt" role="alert">
      <b>{'\u26A0'} The optimizer needs a bit more to work with.</b> {errorMessage(error)}
      <ul>
        {hints.map((hint) => (
          <li key={hint.key}>
            {hint.label}: <Link to={stepPath(hint.step, projectId)}>{hint.action}</Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The confirmation sheet; approving starts the run. */
export function ConfirmSheet({ projectId, inputs, risks, context, starting, startError, onApprove, onCancel }: ConfirmSheetProps) {
  const navigateToStep = useStepNav()
  const sheet = inputs.data
  const ready = sheet?.ready ?? false
  const blocking = sheet?.blocking ?? []

  return (
    <div id="opt-confirm">
      {context === 'first-run' && (
        <div className="card center" style={{ padding: 20 }}>
          <h3 style={{ justifyContent: 'center', marginBottom: 4 }}>No optimization has been run for this project yet</h3>
          <p className="small muted" style={{ margin: 0 }}>
            The solver uses your work packages, team, dependencies and constraints to find the best schedule and assignments.
          </p>
        </div>
      )}
      {context === 'inputs-changed' && (
        <div className="alert warn row between" role="status">
          <span>Your inputs changed after the last run, so its plan is out of date. Check them and run again.</span>
          {onCancel && (
            <button className="btn sec sm" type="button" onClick={onCancel}>
              View the last plan
            </button>
          )}
        </div>
      )}

      <div className={`card${context === 'first-run' ? ' mt' : ''}`}>
        <h3>Check the inputs the solver will use</h3>
        {inputs.isPending && <QuietNote busy>Loading the inputs&hellip;</QuietNote>}
        {inputs.isError && <ProblemAlert title="Couldn't load the inputs" error={inputs.error} onRetry={() => void inputs.refetch()} />}
        {sheet && sheet.rows.length === 0 && <QuietNote>There are no inputs yet. Start with the work packages on step 4.</QuietNote>}
        {sheet && sheet.rows.length > 0 && (
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Input</th>
                  <th>Value</th>
                  <th>Source</th>
                  <th>From</th>
                </tr>
              </thead>
              <tbody id="confirm-rows">
                {sheet.rows.map((row) => (
                  <tr key={row.key}>
                    <td>
                      <b>{row.label}</b>
                    </td>
                    <td>{row.value}</td>
                    <td>
                      <SourceChip source={row.source} />
                    </td>
                    <td className="small muted">{row.from}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div id="confirm-advice">
          <ConstraintCheckBrief risks={risks} projectId={projectId} />
        </div>

        {sheet && !ready && blocking.length > 0 && (
          <div className="cf-banner bad mt">
            <b>{'\u26A0'} Before the solver can run:</b>
            <ul>
              {blocking.map((reason) => (
                <li key={reason}>
                  <Link to={stepPath(blockingStep(reason), projectId)}>{reason}</Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        {startError != null && <StartProblem error={startError} projectId={projectId} />}

        <div className="row between mt">
          <span className="small muted">Inferred values are never used. Approving locks this exact set of inputs.</span>
          <div className="row">
            <span className="gate bad" id="confirm-hint">
              {sheet && !ready ? 'Finish steps 4 and 5 first.' : ''}
            </span>
            {onCancel && context === 'run-again' && (
              <button className="btn sec" type="button" onClick={onCancel}>
                Cancel
              </button>
            )}
            <button className="btn sec" type="button" onClick={() => navigateToStep('constraints')}>
              &larr; Adjust Constraints
            </button>
            <button className="btn" id="btn-approve-inputs" type="button" disabled={!ready || starting} onClick={onApprove}>
              {starting ? 'Starting\u2026' : 'Approve inputs & run'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
