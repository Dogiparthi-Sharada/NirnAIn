/**
 * An input that saves when you leave it (or press Enter), used for every inline edit on the Constraints screen.
 *
 * Purpose: The design edits values in place without a Save button; saving on every keystroke would
 * send half-typed values, so the typed text stays a local draft until the PM is done. Escape drops
 * the draft; a rejected value either falls back to the saved one (explained in a toast) or stays
 * next to an inline message when `onCommit` returns `false`.
 * How it fits: Used by the cards in `screens/constraints/*` for hours, skill names, budget
 * amounts, dates and the specialist rate; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useState, type InputHTMLAttributes } from 'react'

type NativeProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'defaultValue' | 'onChange' | 'onBlur' | 'onKeyDown'>

export interface CommitInputProps extends NativeProps {
  /** The saved value, as text. */
  value: string
  /**
   * Called with the typed text once the PM is done editing and it differs from `value`. Returning
   * `false` rejects it and keeps the typed text in the field.
   */
  onCommit: (text: string) => boolean | void
  /** Called when the field goes back to the saved value (Escape, or the saved value typed again). */
  onRevert?: () => void
}

/** A text, number or date input that commits its draft on blur or Enter. */
export function CommitInput({ value, onCommit, onRevert, ...inputProps }: CommitInputProps) {
  const [draft, setDraft] = useState<string | null>(null)

  /**
   * Saves the draft when it changed; keeps the typed text when the parent refuses it, else drops the draft.
   */
  function commit() {
    if (draft === null) return
    if (draft.trim() === value.trim()) onRevert?.()
    else if (onCommit(draft) === false) return
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
          onRevert?.()
        }
      }}
    />
  )
}
