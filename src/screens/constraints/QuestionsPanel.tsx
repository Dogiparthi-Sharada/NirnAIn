/**
 * "Questions that change the plan" on step 5 (approved mockup views/constraints.js `#qpanel`):
 * confirmed effort from step 4 and "Is <date> a hard deadline?", plus the low-impact settings we
 * checked without asking.
 *
 * Business rules: we only ask when the answer changes the schedule or the cost. "Don't know" is
 * planned with the safer assumption (a hard deadline) and the plan says so. The deadline answer is
 * the same setting as the Deadline card's Must hold / Can flex. Skills are not part of planning
 * (every team member can do every work package), so there is no specialist question.
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Chip } from '@/components/InputsKit'
import { checkedNotAsked, DEADLINE_QUESTION_ID, deadlineChanges, deadlineQuestion, type ConstraintChanges } from '@/lib/constraints'
import { formatDate } from '@/lib/format'
import type { ConstraintInputs, DeadlineAnswerValue } from '@/lib/inputs'
import { stepPath } from '@/lib/steps'

/** Where the effort from step 4 stands. */
export type EffortState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; retry: () => void }
  | { kind: 'ready'; packages: number; openEstimates: number; weeks: number }

interface Option<Value> {
  value: Value
  label: string
}

const DEADLINE_OPTIONS: Option<DeadlineAnswerValue>[] = [
  { value: 'hard', label: 'Hard deadline' },
  { value: 'soft', label: 'Can move' },
  { value: 'unknown', label: "Don't know" },
]

/** "Hard deadline / Can move / Don't know" pills; the picked one is filled. */
function AnswerOptions<Value extends string>({ name, label, options, picked, onPick }: {
  name: string
  label: string
  options: Option<Value>[]
  picked: Value | null | undefined
  onPick: (value: Value) => void
}) {
  return (
    <div className="opts" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          className={picked === option.value ? 'opt picked' : 'opt'}
          type="button"
          data-q={name}
          data-a={option.value}
          aria-pressed={picked === option.value}
          onClick={() => onPick(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** The effort answer: confirmed weeks, or what step 4 still needs. */
function EffortAnswer({ effort, projectId }: { effort: EffortState; projectId: string }) {
  const stepFour = <Link to={stepPath('wbs', projectId)}>step 4</Link>
  if (effort.kind === 'loading') return <span className="muted">Loading the work packages&hellip;</span>
  if (effort.kind === 'error') {
    return (
      <span role="alert">
        Couldn&rsquo;t load the work packages. {effort.message}{' '}
        <button className="btn sec sm" type="button" onClick={effort.retry}>
          Try again
        </button>
      </span>
    )
  }
  if (effort.packages === 0) {
    return (
      <>
        <Chip kind="missing">Needs you</Chip> Add work packages with durations on {stepFour}.
      </>
    )
  }
  if (effort.openEstimates > 0) {
    return (
      <>
        <Chip kind="missing">Needs you</Chip> Confirm the estimates on {stepFour}.
      </>
    )
  }
  return (
    <>
      <Chip kind="confirmed" /> {effort.weeks} weeks across {effort.packages} work packages, confirmed on {stepFour}.
    </>
  )
}

/** One high-impact question card: the question, why we ask, the options and the answer. */
function Question({ id, title, why, children }: { id: string; title: string; why: ReactNode; children: ReactNode }) {
  return (
    <div className="q" id={id}>
      <div className="q-top">
        <span className="tier high">High impact</span>
        <b>{title}</b>
      </div>
      <div className="why">Why we ask: {why}</div>
      {children}
    </div>
  )
}

/** The questions card. `onSave` saves the deadline answer. */
export function QuestionsPanel({ projectId, constraints, effort, onSave }: {
  projectId: string
  constraints: ConstraintInputs
  effort: EffortState
  onSave: (changes: ConstraintChanges) => void
}) {
  const deadline = constraints.deadlineAnswer
  const deadlineDate = constraints.mustFinishBy
  const deadlineTitle = deadlineQuestion(deadlineDate)
  const packages = effort.kind === 'ready' && effort.packages > 0 ? `${effort.packages} ` : ''

  return (
    <div className="card span2" id="qpanel">
      <div className="row between">
        <h3 style={{ margin: 0 }}>Questions that change the plan</h3>
        <span className="small muted">We only ask when the answer changes the schedule or the cost.</span>
      </div>

      <div className="q" id="q-effort">
        <div className="q-top">
          <span className="tier block">Blocking</span>
          <b>Effort for the {packages}work packages</b>
        </div>
        <div className="answer" id="qeff-ans">
          <EffortAnswer effort={effort} projectId={projectId} />
        </div>
      </div>

      <Question
        id={DEADLINE_QUESTION_ID}
        title={deadlineTitle}
        why={
          deadlineDate ? (
            <>
              Hard &rarr; the plan must finish by {formatDate(deadlineDate)}. Can move &rarr; the solver may finish later if that&rsquo;s the only way.
            </>
          ) : (
            <>
              no must-finish-by date is set here, so the plan follows the project&rsquo;s target end date, if it has one. Hard &rarr; the plan must
              finish by it. Can move &rarr; the solver may finish later if that&rsquo;s the only way.
            </>
          )
        }
      >
        <AnswerOptions name="deadline" label={deadlineTitle} options={DEADLINE_OPTIONS} picked={deadline} onPick={(answer) => onSave(deadlineChanges(answer))} />
        <div className="answer" id="qB-ans">
          {(deadline === 'hard' || deadline === 'soft') && <Chip kind="confirmed" />}
          {deadline === 'hard' && ` ${deadlineDate ? formatDate(deadlineDate) : 'The target end date'} is a hard deadline.`}
          {deadline === 'soft' && ' The date can move if that is the only way.'}
          {deadline === 'unknown' && (
            <>
              <Chip kind="assumed" /> Hard deadline (the safer default). The plan will say so.
            </>
          )}
        </div>
      </Question>

      <div className="checked">
        <b>Checked, not asked</b> (low impact, defaults used, all editable below): {checkedNotAsked(constraints)}.
      </div>
    </div>
  )
}
