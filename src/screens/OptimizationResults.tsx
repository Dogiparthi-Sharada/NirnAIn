/**
 * Step 6, Optimization Results: confirm the inputs, solve, and review, apply or export the plan.
 *
 * Purpose: One screen with four states picked from the latest run and the inputs sheet: confirm
 * (nothing runs until the PM approves this exact set of inputs; the server refuses with 409 when
 * they changed since), solving, infeasible (conflicts and priced remedies) and plan. The plan in
 * force says when the inputs changed after it was applied, and a plan solved from other inputs
 * can't be applied.
 * How it fits: Port of the mockup's views/optimize*.js; route `projects/:projectId/optimization`
 * in `App.tsx`; the states live in `screens/optimization/*` and the data in `lib/optimize.ts` and
 * `lib/queries`; layer: screen.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useRef, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { policyRisks } from '@/lib/constraintPolicy'
import {
  comparableInputsHash,
  OUTDATED_PLAN_REASON,
  policyInputsOf,
  shortHash,
  solvedOnOtherInputs,
  useOptimizationInputs,
  useProjectSkillList,
  useStartOptimization,
  type ConstraintAnswers,
  type PlanRun,
  type RunRequest,
} from '@/lib/optimize'
import { isRunning, useConstraints, useLatestRun } from '@/lib/queries'
import { useToast } from '@/components/InputsKit'
import { ConfirmSheet } from './optimization/ConfirmSheet'
import { PlanView } from './optimization/PlanView'
import { InfeasibleState, SolvingState } from './optimization/RunStates'
import { ProblemAlert, QuietNote } from './optimization/ui'

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
  // Set in the click itself: `startRun.isPending` (which disables the buttons) only arrives with
  // the next render, after a double-click's second click (NFR-005).
  const startingRef = useRef(false)
  const [toast, showToast] = useToast({ keepMounted: false })
  // "Run again" re-opens the sheet; "View the last plan" dismisses the inputs-changed sheet.
  const [sheetRequested, setSheetRequested] = useState(false)
  const [stalePlanAccepted, setStalePlanAccepted] = useState(false)

  const run: PlanRun | null = latest.data ?? null
  const status = run?.status ?? null
  const solving = startRun.isPending || isRunning(status)
  const constraints = constraintsQuery.data as ConstraintAnswers | undefined
  // Wait for the skill list so the skill-gap warning doesn't pop in late; if it fails, skip that check.
  const policy = constraints && !skills.isPending ? policyInputsOf(constraints, skills.data, inputs.data) : null
  const risks = policy ? policyRisks(policy) : []
  const runHash = comparableInputsHash(run)
  const checkingStaleness = Boolean(runHash && inputs.isPending)
  // Stale inputs: they changed after the latest run was solved, so the sheet asks again first.
  const inputsChanged = Boolean(runHash && inputs.data?.hash && runHash !== inputs.data.hash)
  const showSheet = !run || sheetRequested || (inputsChanged && !stalePlanAccepted)
  const planShown = !solving && !showSheet && (status === 'optimal' || status === 'feasible')
  // Plan in force: once applied, the plan says so when today's inputs no longer match it.
  const appliedHash = inputs.data?.appliedHash
  const changedSinceApplied = Boolean(run?.version != null && appliedHash && inputs.data?.hash && inputs.data.hash !== appliedHash)
  const outdatedPlan = solvedOnOtherInputs(run, inputs.data?.hash)

  /** Starts a run (with a remedy when re-solving an infeasible plan); success closes the sheet. */
  function start(request: RunRequest = {}) {
    if (startingRef.current) return
    startingRef.current = true
    startRun.mutate(request, {
      onSuccess: () => {
        setSheetRequested(false)
        setStalePlanAccepted(false)
      },
      onSettled: () => {
        startingRef.current = false
      },
    })
  }

  /** Re-opens the confirmation sheet for a fresh run. */
  function runAgain() {
    startRun.reset()
    setSheetRequested(true)
  }

  /** Leaves the sheet and shows the latest run again, even if its inputs changed since. */
  function backToResults() {
    startRun.reset()
    setSheetRequested(false)
    setStalePlanAccepted(true)
  }

  let body: ReactNode
  if (latest.isPending || checkingStaleness) {
    body = <QuietNote busy>Loading the optimization status&hellip;</QuietNote>
  } else if (latest.isError && !run) {
    body = <ProblemAlert title="Couldn't load the optimization status" error={latest.error} onRetry={() => void latest.refetch()} />
  } else if (solving) {
    // While the start request is in flight `run` is still the previous one, so use the sheet's hash.
    const lockedHash = startRun.isPending ? inputs.data?.hash : (run?.options?.inputsHash ?? inputs.data?.hash)
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
        onApprove={() => start({ reviewedHash: inputs.data?.hash })}
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
        onResolve={(remedy) => start({ remedy })}
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
        onUseRemedy={(remedy) => start({ remedy })}
        remedyStarting={startRun.isPending}
        remedyError={startRun.error}
        applyBlocked={outdatedPlan ? { reason: OUTDATED_PLAN_REASON, reasonId: 'opt-plan-outdated-reason' } : undefined}
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
        {planShown && changedSinceApplied && (
          <div className="alert warn row between" id="opt-inputs-changed" role="status">
            <span>
              Inputs changed since Plan v{run?.version}. Run to see the new plan.
            </span>
            <button className="btn sm" type="button" onClick={runAgain}>
              Check the inputs &amp; run
            </button>
          </div>
        )}
        {planShown && outdatedPlan && (
          <div className="alert warn row between" id="opt-plan-outdated" role="status">
            <span id="opt-plan-outdated-reason">{OUTDATED_PLAN_REASON}.</span>
            <button className="btn sm" type="button" onClick={runAgain}>
              Run again
            </button>
          </div>
        )}
        {body}
        {toast}
      </div>
    </section>
  )
}
