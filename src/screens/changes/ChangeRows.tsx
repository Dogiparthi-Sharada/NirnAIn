/**
 * Rows of the step 7 impact card: each detected change (kind, before → after, the verbatim
 * quote from the notes) and the questions it raises, rendered with the mockup's `.chg`,
 * `.flag`, `.q` and `.opt` patterns. Used by `ChangeImpact`, which saves each answer.
 */
import type { ChangeQuestion, ChangeSource, DetectedChange } from '@/lib/changes'
import { Chip, changeTitle, changeValue, kindLabel } from './ui'

/** Saves one answer; ignored while another answer is being saved or after a decision. */
export type PickAnswer = (q: ChangeQuestion, value: string) => void

/** One `.chg` row: kind, title, before → after, the verbatim quote (notes) and any conflict flag. */
export function ChangeRow({
  change: c,
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
  const done = c.kind === 'duration' && c.to === 0
  const showValues = c.kind !== 'scope' && !done && (c.target !== null || c.kind === 'budget' || c.kind === 'deadline')
  // Low confidence = the reader matched loosely; a question always follows, so flag it for checking.
  const chip =
    c.kind === 'scope' ? (
      <Chip k="default">Not re-planned</Chip>
    ) : source !== 'notes' ? (
      <Chip k="confirmed" />
    ) : c.confidence < 0.6 ? (
      <Chip k="inferred">Check</Chip>
    ) : (
      <Chip k="stated" />
    )
  return (
    <div className="chg">
      <div className="kind">{kindLabel(c)}</div>
      <div>
        <b>{changeTitle(c)}</b>
        {showValues && (
          <div className="ba">
            <b>{changeValue(c, c.from)}</b> &rarr; <b>{changeValue(c, c.to)}</b>
          </div>
        )}
        {c.note && <div className="ba">{c.note}</div>}
        {source === 'notes' && c.quote ? (
          <div className="quote">
            “{c.quote}”{c.speaker && <span className="src">{c.speaker}</span>}
          </div>
        ) : (
          <div className="note">{source === 'progress' ? 'Progress update by you' : 'Edited by you'}</div>
        )}
        {flags.map((q) => (
          <div className="flag" key={q.id}>
            <div>{q.text}</div>
            <Options q={q} picked={answers[q.id]} disabled={disabled} onPick={onPick} />
          </div>
        ))}
      </div>
      {chip}
    </div>
  )
}

/** Answer buttons (`.opt`, pressed state for screen readers). Budget options carry `data-budget` in dollars. */
function Options({ q, picked, disabled, onPick }: { q: ChangeQuestion; picked: string | undefined; disabled: boolean; onPick: PickAnswer }) {
  return (
    <div className="opts" role="group" aria-label={q.text}>
      {q.options.map((o) => (
        <button
          key={o.value}
          className={`opt${picked === o.value ? ' picked' : ''}`}
          type="button"
          aria-pressed={picked === o.value}
          disabled={disabled}
          data-budget={q.kind === 'budget' && o.value !== 'keep' ? String(Number(o.value) / 100) : undefined}
          onClick={() => onPick(q, o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A question card (`.q`): tier, the question, why we ask, the options and the chosen answer. */
export function Question({ q, picked, disabled, onPick }: { q: ChangeQuestion; picked: string | undefined; disabled: boolean; onPick: PickAnswer }) {
  const label = q.options.find((o) => o.value === picked)?.label
  return (
    <div className="q">
      <div className="q-top">
        <span className={`tier ${q.tier === 'blocking' ? 'block' : 'high'}`}>{q.tier === 'blocking' ? 'Blocking' : 'High impact'}</span>
        <b>{q.text}</b>
      </div>
      {q.why && <div className="why">Why we ask: {q.why}</div>}
      <Options q={q} picked={picked} disabled={disabled} onPick={onPick} />
      {label && (
        <div className="answer">
          <Chip k="confirmed" /> {label}
        </div>
      )}
    </div>
  )
}
