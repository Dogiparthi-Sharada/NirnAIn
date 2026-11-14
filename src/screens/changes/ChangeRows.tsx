/**
 * Rows of the step 7 impact card: each detected change (kind, before → after, the verbatim
 * quote from the notes) and the questions it raises, rendered with the mockup's `.chg`,
 * `.flag`, `.q` and `.opt` patterns. Used by `ChangeImpact`, which saves each answer.
 */
import type { ChangeQuestion, ChangeSource, DetectedChange } from '@/lib/changes'
import { Chip, changeTitle, changeValue, kindLabel } from './ui'

/** Saves one answer; ignored while another answer is being saved or after a decision. */
export type PickAnswer = (question: ChangeQuestion, value: string) => void

/** One `.chg` row: kind, title, before → after, the verbatim quote (notes) and any conflict flag. */
export function ChangeRow({
  change: change,
  source,
  flags,
  answers,
  disabled,
  onPick,
}: {
  change: DetectedChange
  source: ChangeSource
  flags: ChangeQuestion[]
  answers: Record<string, string>
  disabled: boolean
  onPick: PickAnswer
}) {
  const done = change.kind === 'duration' && change.to === 0
  // A newcomer's details are in the title; a removal still shows their hours going to zero.
  const joins = change.kind === 'team' && change.action === 'add'
  const showValues = change.kind !== 'scope' && !joins && !done && (change.target !== null || change.kind === 'budget' || change.kind === 'deadline')
  // Low confidence = the reader matched loosely; a question always follows, so flag it for checking.
  const chip =
    change.kind === 'scope' ? (
      <Chip kind="default">Not re-planned</Chip>
    ) : source !== 'notes' ? (
      <Chip kind="confirmed" />
    ) : change.confidence < 0.6 ? (
      <Chip kind="inferred">Check</Chip>
    ) : (
      <Chip kind="stated" />
    )
  return (
    <div className="chg">
      <div className="kind">{kindLabel(change)}</div>
      <div>
        <b>{changeTitle(change)}</b>
        {showValues && (
          <div className="ba">
            <b>{changeValue(change, change.from)}</b> &rarr; <b>{changeValue(change, change.to)}</b>
          </div>
        )}
        {change.note && <div className="ba">{change.note}</div>}
        {source === 'notes' && change.quote ? (
          <div className="quote">
            “{change.quote}”{change.speaker && <span className="src">{change.speaker}</span>}
          </div>
        ) : (
          <div className="note">{source === 'progress' ? 'Progress update by you' : 'Edited by you'}</div>
        )}
        {flags.map((question) => (
          <div className="flag" key={question.id}>
            <div>{question.text}</div>
            <Options question={question} picked={answers[question.id]} disabled={disabled} onPick={onPick} />
          </div>
        ))}
      </div>
      {chip}
    </div>
  )
}

/** Answer buttons (`.opt`, pressed state for screen readers). Budget options carry `data-budget` in dollars. */
function Options({ question, picked, disabled, onPick }: { question: ChangeQuestion; picked: string | undefined; disabled: boolean; onPick: PickAnswer }) {
  return (
    <div className="opts" role="group" aria-label={question.text}>
      {question.options.map((option) => (
        <button
          key={option.value}
          className={`opt${picked === option.value ? ' picked' : ''}`}
          type="button"
          aria-pressed={picked === option.value}
          disabled={disabled}
          data-budget={question.kind === 'budget' && option.value !== 'keep' ? String(Number(option.value) / 100) : undefined}
          onClick={() => onPick(question, option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** A question card (`.q`): tier, the question, why we ask, the options and the chosen answer. */
export function Question({ question, picked, disabled, onPick }: { question: ChangeQuestion; picked: string | undefined; disabled: boolean; onPick: PickAnswer }) {
  const label = question.options.find((option) => option.value === picked)?.label
  return (
    <div className="q">
      <div className="q-top">
        <span className={`tier ${question.tier === 'blocking' ? 'block' : 'high'}`}>{question.tier === 'blocking' ? 'Blocking' : 'High impact'}</span>
        <b>{question.text}</b>
      </div>
      {question.why && <div className="why">Why we ask: {question.why}</div>}
      <Options question={question} picked={picked} disabled={disabled} onPick={onPick} />
      {label && (
        <div className="answer">
          <Chip kind="confirmed" /> {label}
        </div>
      )}
    </div>
  )
}
