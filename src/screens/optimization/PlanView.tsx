/**
 * The plan state of Optimization Results (`#opt-plan`; port of the mockup's views/optimize-plan.js).
 *
 * Shows the applied banner, the specialist-rate confirmation, the run's KPIs, the Constraint
 * check, the timeline and the detail tabs, then Export and Apply. Apply stays disabled while
 * the plan books an outside specialist whose rate nobody confirmed; confirming saves
 * `specialistRateConfirmed: true` on the project's constraint set. Applying makes the run the
 * next plan version (v1, v2 …) and opens step 7.
 */
import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '@/lib/api'
import type { PolicyInputs } from '@/lib/constraintPolicy'
import { formatDate, formatMoney } from '@/lib/format'
import {
  DEFAULT_SPECIALIST_RATE_CENTS,
  exportRun,
  shortHash,
  useRefreshAfterApply,
  useRefreshInputs,
  usesSpecialist,
  type ConstraintAnswers,
  type ExportFormat,
  type PlanRun,
} from '@/lib/optimize'
import { useApplyRun, useSaveConstraints } from '@/lib/queries'
import { stepPath, useStepNav } from '@/lib/steps'
import { ConstraintCheckCard } from './ConstraintCheck'
import { daysBetween, shortDay } from './dates'
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
}

/** "$175/h". */
function hourlyRate(cents: number): string {
  return `${formatMoney(cents)}/h`
}

/** The optimal (or best feasible) plan and the actions on it. */
export function PlanView({ projectId, run, constraints, constraintsError, onRetryConstraints, policy, showToast }: PlanViewProps) {
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
  const canApply = !applied && rateKnown && !needsRate && !applyRun.isPending
  const deadline = constraints?.mustFinishBy ?? summary?.deadline ?? null

  function confirmRate() {
    if (!constraints) return
    const confirmed: ConstraintAnswers = { ...constraints, specialistRateConfirmed: true }
    saveConstraints.mutate(confirmed, {
      onSuccess: () => {
        refreshInputs()
        showToast(`Rate confirmed at ${hourlyRate(rateCents)}. The cost is now firm.`)
      },
    })
  }

  function apply() {
    applyRun.mutate(run.id, {
      onSuccess: (appliedRun) => {
        refreshAfterApply()
        const version = (appliedRun as PlanRun).version
        showToast(`Plan${version != null ? ` v${version}` : ''} applied. Step 7, Changes & Versions, is now open.`)
      },
    })
  }

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

  if (!result) {
    return <QuietNote>The solver finished but returned no plan details. Run it again to rebuild the plan.</QuietNote>
  }

  const inputsHash = shortHash(run.options?.inputsHash)
  // Measured against the saved deadline: a move_deadline run's own summary deadline is the moved one.
  const lateDays = summary && deadline ? daysBetween(deadline, summary.completionDate) : 0
  const lateReason = run.options?.remedy === 'move_deadline' ? 'you chose to move the date' : 'the date was allowed to move'

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
                The plan uses the outside specialist rate of <b>{hourlyRate(rateCents)}</b> from Constraints.
              </>
            ) : (
              <>
                The outside specialist&rsquo;s rate isn&rsquo;t in your notes. We used <b>{hourlyRate(rateCents)}</b>, a typical market rate.
              </>
            )}{' '}
            Confirm it before applying, or change it on Constraints.
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

      {lateDays > 0 && deadline ? (
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

      {policy ? (
        <ConstraintCheckCard policy={policy} run={run} />
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
          <button className="btn sec" type="button" onClick={() => void download('pdf')} disabled={exporting !== null}>
            {exporting === 'pdf' ? 'Exporting\u2026' : 'Export PDF Report'}
          </button>
          <button className="btn sec" type="button" onClick={() => void download('msproject')} disabled={exporting !== null}>
            {exporting === 'msproject' ? 'Exporting\u2026' : 'Export to MS Project'}
          </button>
          {applied ? (
            <button className="btn" id="btn-apply" type="button" disabled>
              {'\u2713'} Applied as Plan v{run.version}
            </button>
          ) : (
            <button
              className="btn"
              id="btn-apply"
              type="button"
              onClick={apply}
              disabled={!canApply}
              title={needsRate ? 'Confirm the specialist rate first' : undefined}
            >
              {applyRun.isPending ? 'Applying\u2026' : 'Apply Optimized Plan'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
