/**
 * Step 5, Constraints (port of the approved mockup's views/constraints.js and
 * constraints-team.js), on live data: the limits the plan must respect, the question that changes
 * the plan, the team, the task dependencies, and the gate to step 6. Skills are not part of
 * planning (every team member can do every work package), so the design's skills card is left out.
 *
 * Each limit (budget, deadline, capacity) is Must hold (a hard constraint: the solver
 * never breaks it) or Can flex (a soft constraint: it may bend, with a penalty, if that is the
 * only way); `lib/constraintPolicy.ts` recommends a setting and warns about misleading
 * combinations. The cards live in `screens/constraints/*`; the rules (saving, the gate, the plan
 * lock) in `lib/constraints.ts`.
 *
 * Gate: Run Optimization is never disabled; a blocked click opens `#con-banner` with a link to
 * each missing input. The inputs stay editable after a plan version is applied; a banner then says
 * that changes take effect once step 6 re-runs and the result is applied as the next version.
 */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { Crumb, PlanInForceBanner } from '@/components/InputsKit'
import { errorMessage } from '@/lib/api'
import { policyRisks } from '@/lib/constraintPolicy'
import { gateBlockers, useConstraintEditor, type ConstraintChanges } from '@/lib/constraints'
import { useConstraintInputs, useTeam } from '@/lib/inputs'
import { policyInputsOf, useOptimizationInputs } from '@/lib/optimize'
import { useWbs } from '@/lib/queries'
import { useStepNav } from '@/lib/steps'
import { totalWeeks, wbsBlockers, type WbsTreeNode } from '@/lib/wbs'
import { ConstraintsGate } from './constraints/ConstraintsGate'
import { DependenciesCard } from './constraints/DependenciesCard'
import { BudgetCard, DeadlineCard } from './constraints/LimitCards'
import { QuestionsPanel, type EffortState } from './constraints/QuestionsPanel'
import { TeamEditor } from './constraints/TeamEditor'

const TRAIL = ['Create Project', 'AI Intake', 'Scope Review', 'WBS Builder', 'Constraints']

/** The step 5 screen. */
export default function Constraints() {
  const goToStep = useStepNav()
  const { projectId = '' } = useParams()
  const constraintsQuery = useConstraintInputs(projectId)
  const teamQuery = useTeam(projectId)
  const wbsQuery = useWbs(projectId)
  const sheetQuery = useOptimizationInputs(projectId)
  const editor = useConstraintEditor(projectId)
  const [runClicks, setRunClicks] = useState(0)

  useEffect(() => {
    document.title = 'Constraints · NirnAIn'
  }, [])

  const constraints = constraintsQuery.data
  const policy = constraints ? policyInputsOf(constraints, sheetQuery.data) : null
  const risks = policy ? policyRisks(policy) : []

  const nodes = (wbsQuery.data ?? []) as WbsTreeNode[]
  const workPackages = nodes.filter((node) => node.level === 3).length
  const openEstimates = wbsBlockers(nodes).length
  let effort: EffortState
  if (wbsQuery.isPending) effort = { kind: 'loading' }
  else if (wbsQuery.isError) effort = { kind: 'error', message: errorMessage(wbsQuery.error), retry: () => void wbsQuery.refetch() }
  else effort = { kind: 'ready', packages: workPackages, openEstimates, weeks: totalWeeks(nodes) }

  const serverMissing = sheetQuery.data?.missing ?? []
  const blockers = gateBlockers({
    constraints,
    team: teamQuery.data,
    workPackages: wbsQuery.isSuccess ? workPackages : undefined,
    openEstimates: wbsQuery.isSuccess ? openEstimates : undefined,
    serverMissing,
    serverBlocking: sheetQuery.data?.blocking ?? [],
  })

  function save(changes: ConstraintChanges) {
    editor.mutate(changes)
  }

  /** Never disabled: ready inputs go to step 6, anything missing opens (and focuses) the banner. */
  function runOptimization() {
    setRunClicks((count) => count + 1)
    if (blockers.length === 0) goToStep('optimization')
  }

  return (
    <section className="view on" id="v-constraints">
      <div className="pg">
        <Crumb trail={TRAIL} />
        <h1>Constraints</h1>
        <div className="sub" style={{ marginBottom: 12 }}>
          Set the limits the plan must respect. For each one choose <b>Must hold</b> (a hard constraint: the solver never breaks it) or{' '}
          <b>Can flex</b> (a soft constraint: it may bend, with a penalty, if that is the only way). We recommend a setting for each and warn
          you about risky combinations.
        </div>

        <PlanInForceBanner projectId={projectId} id="con-in-force" />
        {editor.isError && (
          <div className="alert bad row between" role="alert">
            <span>
              <b>Your change wasn&rsquo;t saved.</b> {errorMessage(editor.error)}
            </span>
            <button className="btn sec sm" type="button" onClick={() => editor.reset()}>
              Dismiss
            </button>
          </div>
        )}

        <div className="g2">
          {constraints && policy ? (
            <>
              <QuestionsPanel
                projectId={projectId}
                constraints={constraints}
                effort={effort}
                onSave={save}
              />
              <BudgetCard
                constraints={constraints}
                policy={policy}
                risks={risks}
                onSave={save}
                budgetSource={sheetQuery.data?.rows.find((row) => row.key === 'budget')?.source}
              />
              <DeadlineCard constraints={constraints} policy={policy} risks={risks} onSave={save} />
            </>
          ) : constraintsQuery.isError ? (
            <div className="card span2" id="qpanel" role="alert">
              <b>Couldn&rsquo;t load the constraint settings.</b> {errorMessage(constraintsQuery.error)}{' '}
              <button className="btn sec sm" type="button" onClick={() => void constraintsQuery.refetch()}>
                Try again
              </button>
            </div>
          ) : (
            <div className="card span2" id="qpanel" aria-busy="true">
              <p className="small muted" role="status" style={{ margin: 0 }}>
                Loading the questions and limits&hellip;
              </p>
            </div>
          )}
          <TeamEditor projectId={projectId} policy={policy} risks={risks} onSavePolicy={save} />
          <DependenciesCard projectId={projectId} />
        </div>

        <ConstraintsGate
          blockers={blockers}
          flagged={risks.filter((risk) => risk.level === 'change').length}
          tries={runClicks}
          onRun={runOptimization}
          onBack={() => goToStep('wbs')}
          onOpenWbs={() => goToStep('wbs')}
        />
      </div>
    </section>
  )
}
