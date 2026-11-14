/**
 * The WBS Builder's header actions (mockup views/wbs.js `.hdr .row`): the Chart / List switch,
 * + Add Deliverable, + Add Work Package and AI Validate. Kept apart from `screens/WBSBuilder.tsx`,
 * which owns the data and the API calls, so the screen stays readable.
 *
 * A button that can't be used yet says why (title): a work package goes under a deliverable, and
 * there is nothing to validate before the WBS has nodes.
 */

/** The header actions; `canAddWorkPackage` / `canValidate` false disable those buttons with a reason. */
export default function WbsToolbar({ flat, onFlat, onAddDeliverable, onAddWorkPackage, canAddWorkPackage, onValidate, validating, canValidate }: {
  flat: boolean
  onFlat: (flat: boolean) => void
  onAddDeliverable: () => void
  onAddWorkPackage: () => void
  canAddWorkPackage: boolean
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
      <button className="btn sec" type="button" onClick={onAddDeliverable}>
        + Add Deliverable
      </button>
      <button
        className="btn sec"
        type="button"
        onClick={onAddWorkPackage}
        disabled={!canAddWorkPackage}
        title={canAddWorkPackage ? undefined : 'Add a deliverable first: work packages go under one.'}
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
