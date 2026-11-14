/**
 * The WBS Builder's header actions: Chart / List, + Add Deliverable, + Add Work Package and AI Validate.
 *
 * Purpose: A button that can't be used yet says why (a work package goes under a deliverable;
 * there is nothing to validate before the WBS has nodes), and the add buttons wait while a node
 * saves so a double click never adds it twice.
 * How it fits: Port of the mockup's views/wbs.js `.hdr .row`; rendered by
 * `screens/WBSBuilder.tsx`, which owns the data and the API calls; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

const ADDING_REASON = 'Adding\u2026 wait for the new row before adding another.'

/** The header actions; `canAddWorkPackage` / `canValidate` false disable those buttons with a reason. */
export default function WbsToolbar({ flat, onFlat, onAddDeliverable, onAddWorkPackage, canAddWorkPackage, adding, onValidate, validating, canValidate }: {
  flat: boolean
  onFlat: (flat: boolean) => void
  onAddDeliverable: () => void
  onAddWorkPackage: () => void
  canAddWorkPackage: boolean
  /** A new node is being saved: the add buttons wait. */
  adding: boolean
  onValidate: () => void
  validating: boolean
  canValidate: boolean
}) {
  return (
    <div className="row">
      <span className="seg" role="group" aria-label="View">
        <button className={flat ? undefined : 'on'} type="button" aria-pressed={!flat} onClick={() => onFlat(false)}>
          Chart
        </button>
        <button className={flat ? 'on' : undefined} type="button" aria-pressed={flat} onClick={() => onFlat(true)}>
          List
        </button>
      </span>
      <button className="btn sec" type="button" onClick={onAddDeliverable} disabled={adding} title={adding ? ADDING_REASON : undefined} aria-busy={adding || undefined}>
        + Add Deliverable
      </button>
      <button
        className="btn sec"
        type="button"
        onClick={onAddWorkPackage}
        disabled={adding || !canAddWorkPackage}
        title={adding ? ADDING_REASON : canAddWorkPackage ? undefined : 'Add a deliverable first: work packages go under one.'}
        aria-busy={adding || undefined}
      >
        + Add Work Package
      </button>
      <button
        className="btn"
        id="btn-validate"
        type="button"
        onClick={onValidate}
        disabled={validating || !canValidate}
        title={!validating && !canValidate ? 'Nothing to validate yet: add or generate the WBS first.' : undefined}
      >
        {validating ? 'Validating…' : 'AI Validate'}
      </button>
    </div>
  )
}
