/**
 * The impact of one change on step 7: what changed, the questions it raises, what it touches and the re-plan.
 *
 * Purpose: Answers save immediately and, once all are in, the backend's overrides unlock "Re-plan"
 * against the plan in force. Conflicts with values the PM confirmed are asked, never overwritten
 * ("never invent"). When the re-plan slips the date or finds no plan, the priced ways to keep the
 * date are offered, and a failed re-plan can always be dismissed ("Keep Plan vN") so a change
 * never stays stuck open.
 * How it fits: Rendered by `screens/ChangesVersions.tsx`; uses `ChangeRows.tsx`,
 * `ImpactCards.tsx`, `KeepDate.tsx` and `PlanComparison.tsx`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useRef } from 'react'
import { errorMessage } from '@/lib/api'
import {
  isRunning,
  isSolved,
  keepDateOf,
  useAnswerChange,
  useCompare,
  useDecideChange,
  useReplan,
  type ChangeRequest,
  type KeepDateRemedyId,
  type PlanVersion,
} from '@/lib/changes'
import type { OptimizationRun } from '@/lib/types'
import { ChangeRow, Question, type PickAnswer } from './ChangeRows'
import { Regenerated, RunProblem, Touches } from './ImpactCards'
import { KeepDateCard } from './KeepDate'
import { PlanComparison } from './PlanComparison'
import { RunSteps, useSteps } from './ui'

const SOLVE_STEPS = ['Building model', 'Solving with minimal disruption', 'Preparing the comparison']

/**
 * The active change. `run` is its re-plan (polled by the parent); `onReplanned` records a
 * run id the backend has not linked yet, `onDecided` lets the screen toast and reset.
 */
export function ChangeImpact({
  projectId,
  change,
  current,
  run,
  runError,
  onReplanned,
  onDecided,
}: {
  projectId: string
  change: ChangeRequest
  current: PlanVersion
  run: OptimizationRun | null
  runError: string | null
  onReplanned: (runId: string) => void
  onDecided: (decision: 'approve' | 'reject') => void
}) {
  const answer = useAnswerChange(projectId)
  const replan = useReplan(projectId)
  const decide = useDecideChange(projectId)
  const running = isRunning(run?.status) || replan.isPending
  const solveStep = useSteps(running, SOLVE_STEPS.length)
  const decided = change.status === 'approved' || change.status === 'rejected'
  const solved = isSolved(run?.status)
  const compare = useCompare(change.id, run?.id ?? null, solved && change.status !== 'rejected')
  const compareRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (solved && !decided) compareRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [solved, decided])

  const impact = change.impact
  if (!impact) return null
  const baseVersion = change.baseVersion ?? current.version
  const nextVersion = baseVersion + 1
  /** Saves the PM's answer to one question (not once the change is decided or while an answer saves). */
  const pick: PickAnswer = (question, value) => {
    if (!decided && !answer.isPending) answer.mutate({ changeId: change.id, answers: { [question.id]: value } })
  }
  const flags = impact.questions.filter((question) => question.kind === 'budget' || question.kind === 'deadline')
  const asked = impact.questions.filter((question) => question.kind !== 'budget' && question.kind !== 'deadline')
  const open = impact.questions.filter((question) => !(question.id in change.answers)).length
  // New requirements alone change nothing until their work is in the WBS (step 4): the re-plan
  // then plans it, and approving adds the requirements to the scope.
  const onlyScope = impact.changes.every((detected) => detected.kind === 'scope')
  const ready = change.overrides !== null && impact.changes.length > 0 && !decided
  const notes = change.source === 'notes'
  const count = impact.changes.length
  const lock = decided || answer.isPending
  /** Re-plans the change against the plan in force, optionally with a keep-the-date remedy. */
  const replanWith = (remedy?: KeepDateRemedyId) => {
    if (!change.overrides) return
    replan.mutate(
      { overrides: change.overrides, changeRequestId: change.id, baselineRunId: change.baseRunId, remedy },
      { onSuccess: (replanRun) => onReplanned(replanRun.id) },
    )
  }
  /** Approves the change: the re-planned run becomes the next plan version. */
  const approve = () => decide.mutate({ changeId: change.id, decision: 'approve', runId: run?.id }, { onSuccess: () => onDecided('approve') })
  /** Rejects the change: the plan in force stays. */
  const keep = () => decide.mutate({ changeId: change.id, decision: 'reject' }, { onSuccess: () => onDecided('reject') })
  const deciding = replan.isPending || decide.isPending
  // Newcomers as the re-plan staffs them (hours as answered); leavers by name.
  const team = {
    joining: change.overrides?.addMembers ?? [],
    leaving: impact.changes.filter((detected) => detected.kind === 'team' && detected.action === 'remove').map((detected) => detected.targetLabel ?? 'A team member'),
  }

  let hint: string
  if (replan.isError) hint = errorMessage(replan.error)
  else if (onlyScope) hint = 'New requirements only: add their work in WBS Builder (step 4), then re-plan. Approving adds them to the scope.'
  else if (open > 0) hint = `Answer the item${open === 1 ? '' : 's'} above to continue (${open} open).`
  else hint = `Ready. Plan v${baseVersion} stays in force until you approve v${nextVersion}.`

  return (
    <>
      <div id="impact" className="stack mt">
        <div className="card">
          <div className="row between">
            <h3 style={{ margin: 0 }}>
              What changed{' '}
              {notes ? `(${count} found in the new notes)` : `(${count} ${change.source === 'progress' ? 'update' : 'edit'}${count === 1 ? '' : 's'})`}
            </h3>
            {notes && impact.model === 'rules' && <span className="small muted">Read without AI: only what the notes state</span>}
          </div>
          {count === 0 ? (
            <p className="small muted" style={{ marginBottom: 0 }}>
              We didn’t find a budget, deadline, effort, team or scope change in these notes. Plan v{baseVersion} still holds.
            </p>
          ) : (
            impact.changes.map((detected) => (
              <ChangeRow key={detected.id} change={detected} source={change.source} flags={flags.filter((question) => question.changeId === detected.id)} answers={change.answers} disabled={lock} onPick={pick} />
            ))
          )}
        </div>
        {count > 0 && (
          <>
            <div className="g2">
              <div className="card stack" style={{ gap: 0 }}>
                <h3 style={{ margin: 0 }}>Questions that change the new plan</h3>
                {asked.length === 0 ? (
                  <p className="small muted">{flags.length ? 'Only the choice above.' : 'Nothing to answer: these are your own values.'}</p>
                ) : (
                  asked.map((question) => <Question key={question.id} question={question} picked={change.answers[question.id]} disabled={lock} onPick={pick} />)
                )}
                {answer.isError && (
                  <div className="note bad" role="alert">
                    {errorMessage(answer.error)}
                  </div>
                )}
              </div>
              <Touches change={change} baseVersion={baseVersion} />
            </div>
            <div className="card">
              <h3>What will be regenerated</h3>
              <Regenerated changes={impact.changes} />
              {!decided && (
                <div className="row between mt">
                  <span className={`gate${replan.isError ? ' bad' : ''}`} id="replan-hint" role="status">
                    {hint}
                  </span>
                  <button
                    className="btn"
                    id="btn-replan"
                    type="button"
                    disabled={!ready || running}
                    onClick={() => replanWith()}
                  >
                    {running ? 'Re-planning…' : solved ? 'Re-plan again' : 'Re-plan with minimal disruption'}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {(running || run || runError) && change.status !== 'rejected' && (
        <div id="compare" className="stack mt" ref={compareRef}>
          {running && (
            <div className="card center">
              <div className="ring" aria-hidden="true" />
              <div style={{ display: 'inline-block', textAlign: 'left' }}>
                <RunSteps steps={SOLVE_STEPS} at={solveStep} />
              </div>
              <p className="small muted">OR-Tools is re-solving with Plan v{baseVersion} as the baseline, so as little as possible moves.</p>
            </div>
          )}
          {runError && !running && (
            <div className="alert bad" role="alert">
              We couldn’t load the re-plan. {runError}
            </div>
          )}
          {run && !running && !solved && <RunProblem run={run} />}
          {run && !running && !decided && (solved || run.status === 'infeasible') && (
            <KeepDateCard
              keepDate={keepDateOf(run)}
              infeasible={!solved}
              conflicts={run.iis ?? []}
              busy={deciding}
              onUse={(remedy) => replanWith(remedy)}
              onAccept={approve}
            />
          )}
          {!running && !solved && !decided && (runError || run) && (
            <div className="row end">
              {decide.isError && (
                <span className="gate bad" role="alert">
                  {errorMessage(decide.error)}
                </span>
              )}
              <button className="btn sec" id="btn-keep-current" type="button" disabled={deciding} onClick={keep}>
                Keep Plan v{baseVersion}
              </button>
            </div>
          )}
          {solved && (
            <PlanComparison
              query={compare}
              team={team}
              baseVersion={baseVersion}
              nextVersion={nextVersion}
              approved={change.status === 'approved'}
              busy={decide.isPending}
              error={decide.isError ? errorMessage(decide.error) : null}
              onApprove={approve}
              onKeep={keep}
            />
          )}
        </div>
      )}
    </>
  )
}
