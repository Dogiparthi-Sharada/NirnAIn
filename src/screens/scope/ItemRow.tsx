/**
 * One scope item on Scope Review (step 3): its text (or the editor), the evidence from the
 * notes, its provenance chip and the Accept / Edit / Remove actions (Undo once removed).
 *
 * Business rule: an item can't be saved blank. Clearing the text and pressing Done keeps the
 * editor open with an inline message (QA S-06); Remove is the way to drop an item. Used by
 * `ScopeReview.tsx` in each category and in the "Removed" group.
 */
import { Chip } from '@/components/InputsKit'
import type { EvidenceScopeItem } from '@/lib/inputs'

/** Props of one scope item row; `editError` is the inline message under the editor. */
export interface ScopeItemRowProps {
  item: EvidenceScopeItem
  editing: boolean
  editText: string
  editError: string | null
  /** Shown above the text in the "Removed" group: the category the item came from. */
  categoryLabel?: string
  onEditText: (text: string) => void
  onAccept: () => void
  onRemove: () => void
  onFix: () => void
  onEdit: () => void
  onCancelEdit: () => void
}

/** The evidence under an item's text: the quote, what it is based on, or what goes beyond the notes. */
function Evidence({ item }: { item: EvidenceScopeItem }) {
  const quote = item.sourceQuote
  if (item.evidence === 'stated' && quote) return <div className="quote">&ldquo;{quote}&rdquo;</div>
  if (item.evidence === 'inferred') {
    return (
      <>
        {quote && <div className="quote inf">Based on: &ldquo;{quote}&rdquo;</div>}
        {item.evidenceNote && <div className="note">{item.evidenceNote}</div>}
      </>
    )
  }
  if (item.evidence === 'flagged') {
    return (
      <>
        {quote && <div className="quote">&ldquo;{quote}&rdquo;</div>}
        <div className="note bad">
          {item.evidenceNote ? <>Not in your notes: &ldquo;{item.evidenceNote}&rdquo;</> : 'Says more than your notes.'}
        </div>
      </>
    )
  }
  return null
}

/** The provenance chip: yours, stated, inferred, or flagged (your statement once accepted). */
function ProvenanceChip({ item }: { item: EvidenceScopeItem }) {
  const accepted = item.status === 'accepted'
  if (item.origin === 'manual') return <Chip kind="yours" />
  if (item.evidence === 'flagged') return accepted ? <Chip kind="confirmed">&#10003; Your statement</Chip> : <Chip kind="flag" />
  if (item.evidence === 'stated') return <Chip kind="stated" />
  if (item.evidence === 'inferred') return <Chip kind="inferred" />
  return null
}

/** One scope item with its actions. */
export function ScopeItemRow({
  item,
  editing,
  editText,
  editError,
  categoryLabel,
  onEditText,
  onAccept,
  onRemove,
  onFix,
  onEdit,
  onCancelEdit,
}: ScopeItemRowProps) {
  const accepted = item.status === 'accepted'
  const removed = item.status === 'removed'
  const errorId = `sc-edit-err-${item.id}`
  return (
    <div className={`item${removed ? ' removed' : ''}${accepted ? ' accepted' : ''}`} data-id={item.id} tabIndex={-1}>
      <span className="ic" aria-hidden="true">
        {accepted ? '\u2713' : '\u25CB'}
      </span>
      <div className="item-main">
        {categoryLabel && <div className="small muted">{categoryLabel}</div>}
        {editing ? (
          <>
            <textarea
              className="in item-text"
              value={editText}
              onChange={(event) => onEditText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  onEdit()
                }
                if (event.key === 'Escape') onCancelEdit()
              }}
              aria-label="Item text"
              aria-invalid={editError ? true : undefined}
              aria-describedby={editError ? errorId : undefined}
              maxLength={2000}
              rows={2}
              autoFocus
            />
            {editError && (
              <p className="err" id={errorId} role="alert">
                {editError}
              </p>
            )}
          </>
        ) : (
          <div className="item-text">{item.text}</div>
        )}
        <Evidence item={item} />
      </div>
      <div className="item-side">
        <ProvenanceChip item={item} />
        <div className="acts">
          {removed ? (
            <button className="btn sec sm" type="button" onClick={onRemove}>
              Undo
            </button>
          ) : (
            <>
              {item.evidence === 'flagged' && !accepted && item.suggestedText && (
                <button className="btn sm" data-act="fix" type="button" onClick={onFix}>
                  Use the quoted version
                </button>
              )}
              <button className="btn sec sm" type="button" onClick={onAccept}>
                {accepted ? 'Undo accept' : 'Accept'}
              </button>
              <button className="btn sec sm" type="button" onClick={onEdit}>
                {editing ? 'Done' : 'Edit'}
              </button>
              <button className="btn sec sm" type="button" onClick={onRemove}>
                Remove
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
