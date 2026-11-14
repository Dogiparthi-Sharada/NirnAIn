/**
 * An input that saves when you leave it (or press Enter), used for every inline edit on the
 * Constraints screen: hours per week, skill names, budget amounts, dates and the specialist rate.
 *
 * Why: the approved design edits values in place without a Save button. Saving on every
 * keystroke would send half-typed values to the server, so the typed text stays a local draft
 * until the PM is done. Escape drops the draft. When the parent rejects a value (it shows why in
 * a toast), the field simply falls back to the saved value, like the design's "reverted" input.
 */
import { useState, type InputHTMLAttributes } from 'react'

type NativeProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'defaultValue' | 'onChange' | 'onBlur' | 'onKeyDown'>

export interface CommitInputProps extends NativeProps {
  /** The saved value, as text. */
  value: string
  /** Called with the typed text once the PM is done editing and it differs from `value`. */
  onCommit: (text: string) => void
}

/** A text, number or date input that commits its draft on blur or Enter. */
export function CommitInput({ value, onCommit, ...inputProps }: CommitInputProps) {
  const [draft, setDraft] = useState<string | null>(null)

  function commit() {
    if (draft !== null && draft.trim() !== value.trim()) onCommit(draft)
    setDraft(null)
  }

  return (
    <input
      {...inputProps}
      value={draft ?? value}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          commit()
        } else if (event.key === 'Escape') {
          setDraft(null)
        }
      }}
    />
  )
}
