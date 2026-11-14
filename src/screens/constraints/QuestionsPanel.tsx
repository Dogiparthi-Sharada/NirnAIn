/**
 * "Questions that change the plan" on step 5 (approved mockup views/constraints.js `#qpanel`):
 * confirmed effort from step 4, "Can we bring in an outside specialist?" (with the specialist's
 * hourly rate once the answer is Yes) and "Is <date> a hard deadline?", plus the low-impact
 * settings we checked without asking.
 *
 * Business rules: we only ask when the answer changes the schedule or the cost. "Don't know" is
 * planned with the safer assumption (no specialist, hard deadline) and the plan says so. The
 * deadline answer is the same setting as the Deadline card's Must hold / Can flex. A specialist's
 * cost rests on a rate; until the PM confirms it (or enters the agreed rate) the plan marks the
 * cost as estimated and step 6 asks again before applying.
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Chip } from '@/components/InputsKit'
import {
  checkedNotAsked,
  DEADLINE_QUESTION_ID,
  deadlineChanges,
  deadlineQuestion,
  dollarsText,
  parseDollars,
  SPECIALIST_QUESTION_ID,
  specialistChanges,
  specialistQuestion,
  type ConstraintChanges,
} from '@/lib/constraints'
import { formatDate } from '@/lib/format'
import type { ConstraintInputs, DeadlineAnswerValue, SpecialistAnswer } from '@/lib/inputs'
import { DEFAULT_SPECIALIST_RATE_CENTS } from '@/lib/optimize'
import { stepPath } from '@/lib/steps'
import { toast } from '@/lib/toast'
import { CommitInput } from './CommitInput'

/** Where the effort from step 4 stands. */
export type EffortState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; retry: () => void }
  | { kind: 'ready'; packages: number; openEstimates: number; weeks: number }

interface Option<Value> {
  value: Value
  label: string
}

const SPECIALIST_OPTIONS: Option<SpecialistAnswer>[] = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'unknown', label: "Don't know" },
]
const DEADLINE_OPTIONS: Option<DeadlineAnswerValue>[] = [
  { value: 'hard', label: 'Hard deadline' },
  { value: 'soft', label: 'Can move' },
  { value: 'unknown', label: "Don't know" },
]

function hourlyRate(cents: number): string {
  return `$${(cents / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}/h`
}

/** "Yes / No / Don't know" pills; the picked one is filled. */
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

/** The specialist's hourly rate: estimated until the PM confirms it or enters the agreed one. */
function SpecialistRate({ constraints, onSave }: { constraints: ConstraintInputs; onSave: (changes: ConstraintChanges) => void }) {
  const rateCents = constraints.contractorRateCents ?? DEFAULT_SPECIALIST_RATE_CENTS
  const confirmed = Boolean(constraints.specialistRateConfirmed)

  function commitRate(text: string) {
    const cents = parseDollars(text)
    if (cents === null) {
      onSave({ contractorRateCents: null, specialistRateConfirmed: false })
    } else if (Number.isNaN(cents) || cents <= 0) {
      toast('Enter the hourly rate in dollars, for example 175.')
    } else {
      // A rate the PM typed is the agreed rate, so it counts as confirmed.
      onSave({ contractorRateCents: cents, specialistRateConfirmed: true })
    }
  }

  return (
    <>
      <div className="row mt" id="q-rate">
        <label className="small" htmlFor="con-rate">
          <b>Specialist rate per hour</b>
        </label>
        <CommitInput
          className="in sm"
          id="con-rate"
          inputMode="decimal"
          style={{ width: 110 }}
          value={dollarsText(rateCents)}
          onCommit={commitRate}
        />
        {confirmed ? <Chip kind="confirmed" /> : <Chip kind="inferred">Estimated</Chip>}
        {!confirmed && (
          <button className="btn sm" id="con-rate-confirm" type="button" onClick={() => onSave({ specialistRateConfirmed: true })}>
            Confirm {hourlyRate(rateCents)}
          </button>
        )}
      </div>
      {!confirmed && (
        <p className="small muted" style={{ margin: '6px 0 0' }}>
          {constraints.contractorRateCents == null
            ? `The rate isn\u2019t in your notes. We use ${hourlyRate(rateCents)}, a typical market rate, until you confirm it or enter the agreed rate.`
            : 'Confirm the rate so the plan\u2019s cost is firm.'}
        </p>
      )}
    </>
  )
}

/** One question card: impact tier, the question, why we ask, the options and the answer. */
function Question({ id, tier, title, why, children }: { id: string; tier: 'high' | 'low'; title: string; why: ReactNode; children: ReactNode }) {
  return (
    <div className="q" id={id}>
      <div className="q-top">
        <span className={`tier ${tier}`}>{tier === 'high' ? 'High impact' : 'Low impact'}</span>
        <b>{title}</b>
      </div>
      <div className="why">Why we ask: {why}</div>
      {children}
    </div>
  )
}

/**
 * The questions card. `onSave` saves an answer; `skillGaps` are required skills on the
 * project's list that nobody has; `workPackageGap` is the server noticing a work package that
 * needs a skill nobody on the team has (outside the list), which also makes the question count.
 */
export function QuestionsPanel({ projectId, constraints, skillGaps, workPackageGap, effort, onSave }: {
  projectId: string
  constraints: ConstraintInputs
  skillGaps: string[]
  workPackageGap: boolean
  effort: EffortState
  onSave: (changes: ConstraintChanges) => void
}) {
  const specialist = constraints.specialistAnswer
  const deadline = constraints.deadlineAnswer
  const deadlineDate = constraints.mustFinishBy
  const specialistTitle = specialistQuestion(skillGaps)
  const deadlineTitle = deadlineQuestion(deadlineDate)
  const gapNames = skillGaps.join(', ')
  const specialistMatters = skillGaps.length > 0 || workPackageGap
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
        id={SPECIALIST_QUESTION_ID}
        tier={specialistMatters ? 'high' : 'low'}
        title={specialistTitle}
        why={
          specialistMatters ? (
            <>
              {skillGaps.length > 0 ? (
                <>
                  no one on the team has {gapNames}, which {skillGaps.length === 1 ? 'is a required skill' : 'are required skills'}.
                </>
              ) : (
                'some work packages need a skill no one on the team has.'
              )}{' '}
              Yes &rarr; an outside specialist may do that work, at a cost. No &rarr; the team has to cover it, which can move go-live.
            </>
          ) : (
            <>the team covers every required skill today, so this only matters if that changes. Yes &rarr; an outside specialist may be booked for work nobody on the team can do.</>
          )
        }
      >
        <AnswerOptions name="specialist" label={specialistTitle} options={SPECIALIST_OPTIONS} picked={specialist} onPick={(answer) => onSave(specialistChanges(answer))} />
        <div className="answer" id="qA-ans">
          {specialist === 'yes' && (
            <>
              <Chip kind="confirmed" /> Outside specialists allowed.
            </>
          )}
          {specialist === 'no' && (
            <>
              <Chip kind="confirmed" /> No outside contractors.
            </>
          )}
          {specialist === 'unknown' && (
            <>
              <Chip kind="assumed" /> No outside contractors (the safer default). The plan will say so.
            </>
          )}
        </div>
        {specialist === 'yes' && <SpecialistRate constraints={constraints} onSave={onSave} />}
      </Question>

      <Question
        id={DEADLINE_QUESTION_ID}
        tier="high"
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
