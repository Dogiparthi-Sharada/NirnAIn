/**
 * Step 6, Optimization Results (port of the approved mockup's views/optimize*.js).
 *
 * One screen, four states, picked from the latest run and the inputs sheet:
 * 1. confirm — "Check the inputs the solver will use": shown when there is no run yet, when the
 *    inputs changed after the latest run, or after "Run again". Approving starts a run.
 * 2. solving — while the run is queued or running (`useLatestRun` polls it).
 * 3. infeasible — the conflicting constraints and priced remedies; a remedy starts a new run.
 * 4. plan — KPIs, Constraint check, timeline, details, rate confirmation, Apply and Export.
 * A failed or timed-out run shows its error with "Run again".
 */
import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { policyRisks } from '@/lib/constraintPolicy'
import {
  policyInputsOf,
  shortHash,
  useOptimizationInputs,
  useProjectSkillList,
  useStartOptimization,
  type ConstraintAnswers,
  type PlanRun,
  type RemedyId,
} from '@/lib/optimize'
import { useConstraints, useLatestRun } from '@/lib/queries'
import { ConfirmSheet } from './optimization/ConfirmSheet'
import { PlanView } from './optimization/PlanView'
import { InfeasibleState, SolvingState } from './optimization/RunStates'
import { ProblemAlert, QuietNote, useToast } from './optimization/ui'

/** Header chip: "OPTIMAL OR-Tools · 2.4s" (amber for a feasible, not proven optimal, plan). */
function SolverChip({ run }: { run: PlanRun }) {
  const optimal = run.status === 'optimal'
  return (
    <span className="solver" id="opt-badge">
      <b style={optimal ? undefined : { color: 'var(--amber)' }}>{run.status.toUpperCase()}</b>
      {run.solver || 'OR-Tools'}
      {run.solveSeconds != null ? ` \u00B7 ${run.solveSeconds.toFixed(1)}s` : ''}
    </span>
  )
}

/** Step 6 screen: confirm the inputs, solve, then review and apply the plan. */
export default function OptimizationResults() {
  const { projectId = '' } = useParams()
  const latest = useLatestRun(projectId)
  const inputs = useOptimizationInputs(projectId)
  const constraintsQuery = useConstraints(projectId)
  const skills = useProjectSkillList(projectId)
  const startRun = useStartOptimization(projectId)
  const [toast, showToast] = useToast()
  // "Run again" re-opens the sheet; "View the last plan" dismisses the inputs-changed sheet.
  const [sheetRequested, setSheetRequested] = useState(false)
  const [stalePlanAccepted, setStalePlanAccepted] = useState(false)

  const run: PlanRun | null = latest.data ?? null
  const status = run?.status ?? null
  const solving = startRun.isPending || status === 'queued' || status === 'running'
  const constraints = constraintsQuery.data as ConstraintAnswers | undefined
  const policy = constraints ? policyInputsOf(constraints, skills.data, inputs.data) : null
  const risks = policy ? policyRisks(policy) : []
  // An applied plan is the baseline; its inputs are locked, so only proposals can go stale.
  const runHash = run?.options?.inputsHash
  const inputsChanged = Boolean(run && run.version == null && runHash && inputs.data?.hash && runHash !== inputs.data.hash)
  const showSheet = !run || sheetRequested || (inputsChanged && !stalePlanAccepted)
  const planShown = !solving && !showSheet && (status === 'optimal' || status === 'feasible')

  function start(remedy?: RemedyId) {
    startRun.mutate(remedy ? { remedy } : {}, {
      onSuccess: () => {
        setSheetRequested(false)
        setStalePlanAccepted(false)
      },
    })
  }

  function runAgain() {
    startRun.reset()
    setSheetRequested(true)
  }

  function backToResults() {
    startRun.reset()
    setSheetRequested(false)
    setStalePlanAccepted(true)
  }

  let body: ReactNode
  if (latest.isPending) {
    body = <QuietNote busy>Loading the optimization status&hellip;</QuietNote>
  } else if (latest.isError && !run) {
    body = <ProblemAlert title="Couldn't load the optimization status" error={latest.error} onRetry={() => void latest.refetch()} />
  } else if (solving) {
    // While the start request is in flight `run` is still the previous one, so use the sheet's hash.
    const lockedHash = startRun.isPending ? inputs.data?.hash : (runHash ?? inputs.data?.hash)
    body = <SolvingState running={!startRun.isPending && status === 'running'} inputsHash={shortHash(lockedHash)} />
  } else if (showSheet) {
    body = (
      <ConfirmSheet
        projectId={projectId}
        inputs={inputs}
        risks={risks}
        context={!run ? 'first-run' : sheetRequested ? 'run-again' : 'inputs-changed'}
        starting={startRun.isPending}
        startError={startRun.error}
        onApprove={() => start()}
        onCancel={run ? backToResults : undefined}
      />
    )
  } else if (status === 'infeasible' && run) {
    body = (
      <InfeasibleState
        projectId={projectId}
        run={run}
        resolving={startRun.isPending}
        resolveError={startRun.error}
        onResolve={(remedy) => start(remedy)}
      />
    )
  } else if (planShown && run) {
    body = (
      <PlanView
        projectId={projectId}
        run={run}
        constraints={constraints}
        constraintsError={constraintsQuery.error}
        onRetryConstraints={() => void constraintsQuery.refetch()}
        policy={policy}
        showToast={showToast}
      />
    )
  } else {
    const timedOut = status === 'timeout'
    body = (
      <div className="alert bad row between" role="alert">
        <span>
          <b>{timedOut ? 'The solver timed out.' : 'The optimization failed.'}</b>{' '}
          {run?.error || (timedOut ? 'No plan was found within the time limit.' : 'Something went wrong while solving.')}
        </span>
        <button className="btn sm" type="button" onClick={runAgain}>
          Run again
        </button>
      </div>
    )
  }

  const canRunAgain = run !== null && !solving && !showSheet

  return (
    <section className="view on" id="v-optimize">
      <div className="pg">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; Create Project &rsaquo; AI Intake &rsaquo; Scope Review &rsaquo; WBS &rsaquo; Constraints
          &rsaquo; Optimization Results
        </div>
        <div className="hdr">
          <div>
            <h1>Optimization Results</h1>
            <div className="sub" id="opt-sub">
              {planShown
                ? 'Solver completed. Review recommended plan adjustments before finalizing.'
                : 'Run the OR-Tools solver against your WBS, team and constraints.'}
            </div>
          </div>
          {canRunAgain && (
            <div className="row">
              {planShown && run && <SolverChip run={run} />}
              <button className="btn sec sm" type="button" onClick={runAgain}>
                Run again
              </button>
            </div>
          )}
        </div>
        {body}
        {toast}
      </div>
    </section>
  )
}
