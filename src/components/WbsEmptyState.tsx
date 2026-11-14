/**
 * What the WBS Builder shows in place of the table while a project has no WBS.
 *
 * Purpose: The PM always sees why the table is empty and what to do: loading, a load error, AI
 * drafting the tree from the accepted scope, a failed generation with Try again, or the hint to
 * start by hand.
 * How it fits: Rendered by `screens/WBSBuilder.tsx`; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

/** One status line for the empty table; `onRetry` starts the AI generation again. */
export default function WbsEmptyState({
  loading,
  loadError,
  generating,
  generateError,
  onRetry,
}: {
  loading: boolean
  loadError: string | null
  generating: boolean
  generateError: string | null
  onRetry: () => void
}) {
  if (loading) return <p className="small muted" role="status">Loading the WBS…</p>
  if (loadError) return <p className="small" role="alert" style={{ color: 'var(--red)' }}>{loadError}</p>
  if (generating) return <p className="small muted" role="status" aria-busy="true">AI is building the WBS from the accepted scope…</p>
  if (generateError) {
    return (
      <p className="small" role="alert">
        <span style={{ color: 'var(--red)' }}>{generateError}</span>{' '}
        <button className="linkbtn" type="button" onClick={onRetry}>
          Try again
        </button>{' '}
        or start by hand with + Add Deliverable.
      </p>
    )
  }
  return <p className="small muted">No WBS yet. Use + Add Deliverable to start.</p>
}
