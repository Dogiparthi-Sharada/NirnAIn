/**
 * The plan state of Optimization Results: KPIs, ways to keep the date, Constraint check, timeline, details, Export and Apply.
 *
 * Purpose: Apply stays disabled while the plan books a contractor at a rate nobody confirmed;
 * confirming saves that rate as the project's contractor rate (the default for contractors on step
 * 5). Applying makes the run the next plan version (v1, v2 …) and opens step 7.
 * How it fits: Port of the mockup's views/optimize-plan.js (`#opt-plan`); rendered by
 * `screens/OptimizationResults.tsx`; composes `KeepDateCard`, `ConstraintCheck`, `PlanGantt` and
 * `PlanDetails`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '@/lib/api'
import type { PolicyInputs } from '@/lib/constraintPolicy'
import { formatDate, formatMoney } from '@/lib/format'
import {
  dateSlipOf,
  DEFAULT_SPECIALIST_RATE_CENTS,
  exportRun,
  shortHash,
  useRefreshAfterApply,
  useRefreshInputs,
  usesSpecialist,
  type ConstraintAnswers,
  type ExportFormat,
  type PlanRun,
  type RemedyId,
} from '@/lib/optimize'
import { useApplyRun, useSaveConstraints } from '@/lib/queries'
import { stepPath, useStepNav } from '@/lib/steps'
import { ConstraintCheckCard } from './ConstraintCheck'
import { daysBetween, shortDay } from './dates'
import { KeepDateCard, type AcceptState } from './KeepDateCard'
import { PlanDetails } from './PlanDetails'
import { PlanGantt } from './PlanGantt'
import { ProblemAlert, QuietNote } from './ui'

interface PlanViewProps {
  projectId: string
  run: PlanRun
  /** The saved constraint set (undefined while loading or when it failed to load). */
  constraints: ConstraintAnswers | undefined
  constraintsError: unknown
  onRetryConstraints: () => void
  /** Inputs for the policy rules, once the constraint set has loaded. */
  policy: PolicyInputs | null
  showToast: (message: string) => void
  /** Re-runs the solver with one of the plan's priced remedies. */
  onUseRemedy: (remedy: RemedyId) => void
  remedyStarting: boolean
  remedyError: unknown
  /** Why Apply can't be used (the plan was solved from other inputs) and the id of the element saying so. */
  applyBlocked?: { reason: string; reasonId: string }
}

/** "$175/h". */
function hourlyRate(cents: number): string {
  return `${formatMoney(cents)}/h`
}

/** The optimal (or best feasible) plan and the actions on it. */
export function PlanView({
  projectId,
  run,
  constraints,
  constraintsError,
  onRetryConstraints,
  policy,
  showToast,
  onUseRemedy,
  remedyStarting,
  remedyError,
  applyBlocked,
}: PlanViewProps) {
  const navigateToStep = useStepNav()
  const applyRun = useApplyRun(projectId)
  const saveConstraints = useSaveConstraints(projectId)
  const refreshAfterApply = useRefreshAfterApply(projectId)
  const refreshInputs = useRefreshInputs(projectId)
  const [exporting, setExporting] = useState<ExportFormat | null>(null)
  const [exportError, setExportError] = useState<unknown>(null)

  const result = run.result
  const summary = result?.summary ?? null
  const applied = run.version != null
  const specialist = usesSpecialist(run)
  const rateCents = constraints?.contractorRateCents ?? summary?.specialists[0]?.rateCents ?? DEFAULT_SPECIALIST_RATE_CENTS
  // Until the constraint set loads we can't tell whether the rate is confirmed, so Apply waits.
  const rateKnown = !specialist || constraints !== undefined
  const needsRate = specialist && !applied && constraints !== undefined && !constraints.specialistRateConfirmed
  const canApply = !applied && !applyBlocked && rateKnown && !needsRate && !applyRun.isPending
  const deadline = constraints?.mustFinishBy ?? summary?.deadline ?? null

  /** Saves the current constraint set with the shown contractor rate as the confirmed one. */
  function confirmRate() {
    if (!constraints) return
    const confirmed: ConstraintAnswers = { ...constraints, contractorRateCents: rateCents, specialistRateConfirmed: true }
    saveConstraints.mutate(confirmed, {
      onSuccess: () => {
        refreshInputs()
        showToast(`Rate confirmed at ${hourlyRate(rateCents)}. The cost is now firm.`)
      },
    })
  }

  /** Makes this run the next plan version. */
  function apply() {
    applyRun.mutate(run.id, {
      onSuccess: (appliedRun: PlanRun) => {
        refreshAfterApply()
        const version = appliedRun.version
        showToast(`Plan${version != null ? ` v${version}` : ''} applied. Step 7, Changes & Versions, is now open.`)
      },
    })
  }

  /** Downloads the plan in one format, reporting failures under the actions. */
  async function download(format: ExportFormat) {
    setExporting(format)
    setExportError(null)
    try {
      await exportRun(projectId, run.id, format)
    } catch (error) {
      setExportError(error)
    } finally {
      setExporting(null)
    }
  }

  /** Why an export button waits: the other format is still downloading. */
  function exportBusy(format: ExportFormat): string | undefined {
    return exporting !== null && exporting !== format ? 'Wait for the other export to finish.' : undefined
  }

  if (!result) {
    return <QuietNote>The solver finished but returned no plan details. Run it again to rebuild the plan.</QuietNote>
  }

  const inputsHash = shortHash(run.options?.inputsHash)
  // Measured against the saved deadline: a move_deadline run's own summary deadline is the moved one.
  const lateDays = summary && deadline ? daysBetween(deadline, summary.completionDate) : 0
  const lateReason = run.options?.remedy === 'move_deadline' ? 'you chose to move the date' : 'the date was allowed to move'
  const slip = dateSlipOf(run)
  let accept: AcceptState
  if (applied) accept = { kind: 'applied', version: run.version ?? 0 }
  else if (applyBlocked) accept = { kind: 'blocked', reason: applyBlocked.reason }
  else if (needsRate) accept = { kind: 'blocked', reason: 'Confirm the contractor rate above first.' }
  else if (!rateKnown) accept = { kind: 'blocked', reason: 'Checking the contractor rate\u2026' }
  else accept = { kind: 'ready', applying: applyRun.isPending, onAccept: apply }

  return (
    <div id="opt-plan">
      {applied && (
        <div className="banner row between" role="status">
          <span>
            {'\u2713'} <b>Plan v{run.version} applied</b>
            {run.appliedAt ? ` ${formatDate(run.appliedAt)}` : ''}
            {inputsHash && (
              <>
                {' \u00B7 inputs hash '}
                <span className="mono">{inputsHash}</span>
              </>
            )}
            . This is now the baseline; changes are measured against it.
          </span>
          <Link className="btn sm" to={stepPath('changes', projectId)}>
            Report a change &rarr;
          </Link>
        </div>
      )}

      {needsRate && (
        <div className="alert warn row between" style={{ marginBottom: 16 }}>
          <span>
            {constraints?.contractorRateCents != null ? (
              <>
                The plan books a contractor at the project&rsquo;s contractor rate of <b>{hourlyRate(rateCents)}</b>.
              </>
            ) : (
              <>
                The plan books a contractor at <b>{hourlyRate(rateCents)}</b>, an estimate (a typical market rate).
              </>
            )}{' '}
            Confirm it before applying, or add the contractor to the team on step 5 with their agreed rate and run again.
          </span>
          <button className="btn sm" id="btn-rate" type="button" onClick={confirmRate} disabled={saveConstraints.isPending}>
            {saveConstraints.isPending ? 'Confirming\u2026' : `Confirm ${hourlyRate(rateCents)}`}
          </button>
        </div>
      )}
      {saveConstraints.isError && <ProblemAlert title="The rate wasn't confirmed" error={saveConstraints.error} onRetry={confirmRate} />}
      {specialist && constraintsError != null && constraints === undefined && (
        <ProblemAlert title="Couldn't check the specialist rate" error={constraintsError} onRetry={onRetryConstraints} />
      )}

      {slip ? null : lateDays > 0 && deadline ? (
        <div className="alert warn">
          Best plan found within your constraints. It finishes after {shortDay(deadline)} because {lateReason}.
        </div>
      ) : (
        run.status === 'feasible' && <div className="alert warn">Best plan found within the time limit. It may not be the absolute optimum.</div>
      )}

      {result.kpis.length > 0 && (
        <div className="kpis">
          {result.kpis.map((kpi) => (
            <div className="kpi" key={kpi.label}>
              <div className="k">{kpi.label}</div>
              <div className="v">{kpi.value}</div>
              {kpi.sub && <div className="s">{kpi.sub}</div>}
            </div>
          ))}
        </div>
      )}

      {slip && <KeepDateCard slip={slip} starting={remedyStarting} startError={remedyError} onUse={onUseRemedy} accept={accept} />}

      {policy ? (
        <ConstraintCheckCard policy={policy} run={run} dateSlipShown={slip !== null} />
      ) : constraintsError != null ? (
        <ProblemAlert title="Couldn't load the constraint check" error={constraintsError} onRetry={onRetryConstraints} />
      ) : (
        <div className="card advice">
          <QuietNote busy>Checking your Must hold / Can flex choices&hellip;</QuietNote>
        </div>
      )}

      <div className="card mt">
        <h3>Timeline</h3>
        <PlanGantt schedule={result.schedule ?? []} deadline={deadline} />
      </div>

      <PlanDetails result={result} />

      {exportError != null && <ProblemAlert title="The export failed" error={exportError} />}
      {applyRun.isError && (
        <div className="alert bad" role="alert">
          <b>The plan wasn&rsquo;t applied.</b> {errorMessage(applyRun.error)}
        </div>
      )}
      <div className="row between mt">
        <button className="btn sec" type="button" onClick={() => navigateToStep('constraints')}>
          &larr; Adjust Constraints
        </button>
        <div className="row">
          <button className="btn sec" type="button" onClick={() => void download('pdf')} disabled={exporting !== null} title={exportBusy('pdf')}>
            {exporting === 'pdf' ? 'Exporting\u2026' : 'Export PDF Report'}
          </button>
          <button className="btn sec" type="button" onClick={() => void download('msproject')} disabled={exporting !== null} title={exportBusy('msproject')}>
            {exporting === 'msproject' ? 'Exporting\u2026' : 'Export to MS Project'}
          </button>
          {applied ? (
            <button className="btn" id="btn-apply" type="button" disabled title="This plan is already applied.">
              {'\u2713'} Applied as Plan v{run.version}
            </button>
          ) : (
            <button
              className="btn"
              id="btn-apply"
              type="button"
              onClick={apply}
              disabled={!canApply}
              title={
                applyBlocked?.reason ?? (needsRate ? 'Confirm the contractor rate first' : !rateKnown ? 'Checking the contractor rate\u2026' : undefined)
              }
              aria-describedby={applyBlocked?.reasonId}
            >
              {applyRun.isPending ? 'Applying\u2026' : 'Apply Optimized Plan'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
