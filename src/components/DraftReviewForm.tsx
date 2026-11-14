/**
 * The pre-filled project form of "Create from a transcript or document" (mockup create-upload.js,
 * `#pf-form`): the four numbered sections of the manual form, where every value AI read from the
 * source shows its provenance chip and the sentence it came from.
 *
 * Presentational only: `screens/CreateFromUpload.tsx` owns the state and the Create / Save Draft
 * flow; the provenance rules live in `lib/draftReview.ts`.
 */
import type { ReactNode } from 'react'
import { Field, OptionsSelect, SectionHead, SectionHeadBody } from '@/components/CreateForm'
import { DEPARTMENTS } from '@/lib/createForm'
import { displayValue, markOf, REVIEW_IDS, REVIEW_LABELS, type Mark, type ReviewKey, type ReviewState } from '@/lib/draftReview'
import {
  METHODOLOGIES,
  METHODOLOGY_LABELS,
  PRIORITIES,
  PRIORITY_LABELS,
  PROJECT_TYPES,
  PROJECT_TYPE_LABELS,
  VISIBILITIES,
  VISIBILITY_LABELS,
  type Methodology,
  type Priority,
  type ProjectType,
  type Visibility,
} from '@/lib/types'

/** Settings the source never fills in; they start at the defaults, like the manual form. */
export interface ReviewSettings {
  type: ProjectType
  pmUserId: string
  priority: Priority
  methodology: Methodology
  visibility: Visibility
}

const CHIPS: Record<Mark, [className: string, label: string]> = {
  stated: ['st-stated', 'Stated'],
  inferred: ['st-inferred', 'Inferred'],
  missing: ['st-missing', 'Not found'],
  default: ['st-default', 'Default'],
  confirmed: ['st-confirmed', '✓ Confirmed'],
  set: ['st-confirmed', '✓ Confirmed'],
  edited: ['st-yours', 'Edited by you'],
}

function Chip({ mark }: { mark: Mark }) {
  const [className, label] = CHIPS[mark]
  return <span className={`chip ${className}`}>{label}</span>
}

const DefaultChip = () => <Chip mark="default" />

/** The pre-filled form; `invalid` lists the fields to outline red after a blocked Create. */
export default function DraftReviewForm({
  review,
  sourceName,
  invalid,
  onChange,
  onConfirm,
  settings,
  onSettings,
  pmOptions,
}: {
  review: ReviewState
  sourceName: string
  invalid: ReadonlySet<ReviewKey>
  onChange: (key: ReviewKey, value: string) => void
  onConfirm: (key: ReviewKey) => void
  settings: ReviewSettings
  onSettings: (patch: Partial<ReviewSettings>) => void
  pmOptions: { id: string; label: string }[]
}) {
  /** Chip in the label, plus the quote and the "Confirm …" action under the control. */
  function provenance(key: ReviewKey): { chip: ReactNode; after: ReactNode } {
    const item = review[key]
    const mark = markOf(item)
    const sourceTag = <span className="src">{sourceName}</span>
    const quote = item.quote ? (
      mark === 'stated' || mark === 'edited' ? (
        <div className="quote">
          “{item.quote}” {sourceTag}
        </div>
      ) : (
        <div className="quote inf">
          Based on: “{item.quote}” {sourceTag}
        </div>
      )
    ) : null
    let note: ReactNode = null
    if (mark === 'inferred') {
      note = (
        <div className="note" id={`${REVIEW_IDS[key]}-note`}>
          {item.quote ? 'Worked out from your notes, not stated in them. ' : 'Not quoted from your notes. '}
          <button className="linkbtn" id={`pf-fix-${REVIEW_IDS[key].slice(3)}`} type="button" onClick={() => onConfirm(key)}>
            Confirm {displayValue(key, item.value)}
          </button>
        </div>
      )
    } else if (mark === 'missing' || mark === 'default') {
      note = item.origin === 'missing' ? <div className="note">Not in your notes.</div> : null
    } else if (mark === 'confirmed') {
      note = <div className="note">Confirmed by you.</div>
    } else if (mark === 'set') {
      note = <div className="note">Set by you.</div>
    }
    return {
      chip: <Chip mark={mark} />,
      after: (
        <>
          {mark !== 'missing' && mark !== 'set' && quote}
          {note}
        </>
      ),
    }
  }

  function inputProps(key: ReviewKey) {
    const bad = invalid.has(key)
    return {
      id: REVIEW_IDS[key],
      className: bad ? 'in invalid' : 'in',
      'aria-invalid': bad || undefined,
      value: review[key].value,
      onChange: (event: { target: { value: string } }) => onChange(key, event.target.value),
    }
  }

  const field = (key: ReviewKey, control: ReactNode, required = false) => (
    <Field id={REVIEW_IDS[key]} label={REVIEW_LABELS[key]} required={required} className="field pf" {...provenance(key)}>
      {control}
    </Field>
  )

  const departments = DEPARTMENTS.includes(review.department.value as (typeof DEPARTMENTS)[number])
    ? [...DEPARTMENTS]
    : [review.department.value, ...DEPARTMENTS].filter(Boolean)

  return (
    <div className="stack" id="pf-form">
      <section className="card cf-sec" aria-labelledby="pf-sec-1">
        <SectionHead number={1} id="pf-sec-1" title="About the project" help="Name, type and description, from your source." required />
        <div className="g2">
          {field('name', <input {...inputProps('name')} maxLength={200} autoComplete="off" aria-required="true" />, true)}
          <Field id="pf-type" label="Project Type" className="field pf" chip={<DefaultChip />}>
            <OptionsSelect id="pf-type" className="in" options={PROJECT_TYPES} labels={PROJECT_TYPE_LABELS} value={settings.type} onValue={(value) => onSettings({ type: value })} />
          </Field>
        </div>
        <div className="mt">{field('description', <textarea {...inputProps('description')} rows={3} aria-required="true" />, true)}</div>
      </section>

      <section className="card cf-sec" aria-labelledby="pf-sec-2">
        <SectionHead number={2} id="pf-sec-2" title="Timeline & budget" help="Values AI could not find are left blank for you." required />
        <div className="g3">
          {field('startDate', <input {...inputProps('startDate')} type="date" aria-required="true" />, true)}
          {field('targetEndDate', <input {...inputProps('targetEndDate')} type="date" min={review.startDate.value || undefined} aria-required="true" />, true)}
          {field('budget', <input {...inputProps('budget')} inputMode="decimal" placeholder="e.g. 375,000" autoComplete="off" aria-required="true" />, true)}
        </div>
      </section>

      <section className="card cf-sec" aria-labelledby="pf-sec-3">
        <SectionHead number={3} id="pf-sec-3" title="People" help="Who owns it. You add the delivery team on step 5." />
        <div className="g3">
          <Field id="pf-pm" label="Project Manager" className="field pf" chip={<DefaultChip />}>
            <select id="pf-pm" className="in" value={settings.pmUserId} onChange={(event) => onSettings({ pmUserId: event.target.value })}>
              {pmOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
          {field(
            'department',
            <select {...inputProps('department')}>
              {departments.map((department) => (
                <option key={department} value={department}>
                  {department}
                </option>
              ))}
            </select>,
          )}
          {field('sponsor', <input {...inputProps('sponsor')} maxLength={200} autoComplete="off" placeholder="e.g. VP of Engineering, Robert Walsh" />)}
        </div>
      </section>

      <details className="card cf-sec cf-more">
        <summary className="cf-sec-head">
          <SectionHeadBody number={4} title="More details" help="Priority, methodology, visibility. Defaults used; not in your notes." />
        </summary>
        <div className="g3 mt">
          <Field id="pf-prio" label="Priority" className="field pf" chip={<DefaultChip />}>
            <OptionsSelect id="pf-prio" className="in" options={PRIORITIES} labels={PRIORITY_LABELS} value={settings.priority} onValue={(value) => onSettings({ priority: value })} />
          </Field>
          <Field id="pf-meth" label="Methodology" className="field pf" chip={<DefaultChip />}>
            <OptionsSelect id="pf-meth" className="in" options={METHODOLOGIES} labels={METHODOLOGY_LABELS} value={settings.methodology} onValue={(value) => onSettings({ methodology: value })} />
          </Field>
          <Field id="pf-vis" label="Visibility" className="field pf" chip={<DefaultChip />}>
            <OptionsSelect id="pf-vis" className="in" options={VISIBILITIES} labels={VISIBILITY_LABELS} value={settings.visibility} onValue={(value) => onSettings({ visibility: value })} />
          </Field>
        </div>
      </details>
    </div>
  )
}
