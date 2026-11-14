/**
 * Step 1a, Create Project by hand: four numbered sections, the "Required to continue" checklist and a gap banner.
 *
 * Purpose: Continue and Save Draft are never disabled: a blocked click lists every gap in the
 * banner (each a link that focuses its field) and outlines the fields red. Continue creates the
 * project, uploads any attached files as project documents and opens AI Project Intake; Save Draft
 * needs only a name.
 * How it fits: Port of the mockup's create-form.js; route `projects/new/form` in `App.tsx`; rules
 * in `lib/createForm.ts`, shared pieces in `components/CreateForm.tsx`; layer: screen.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useOrg } from '@/org/useOrg'
import { useCreateProject } from '@/lib/queries'
import { errorMessage, isApiError } from '@/lib/api'
import { usePendingUploads, usePmOptions } from '@/lib/create'
import {
  DEPARTMENTS,
  FIELD_IDS,
  FIELD_LABELS,
  FIELD_ORDER,
  formatBudgetInput,
  IN_MORE_DETAILS,
  initialProjectForm,
  parseBudgetToCents,
  REQUIRED_FIELDS,
  serverFieldErrors,
  toProjectCreate,
  validateProjectForm,
  type FieldErrors,
  type FieldName,
  type FormState,
  type Intent,
} from '@/lib/createForm'
import {
  METHODOLOGIES,
  METHODOLOGY_LABELS,
  PRIORITIES,
  PRIORITY_LABELS,
  PROJECT_TYPES,
  PROJECT_TYPE_LABELS,
  VISIBILITIES,
  VISIBILITY_LABELS,
} from '@/lib/types'
import DocumentUpload from '@/components/DocumentUpload'
import { Field, GapBanner, OptionsSelect, RequiredChecklist, SectionHead, SectionHeadBody, StatusChip, useBannerFocus } from '@/components/CreateForm'

/** The manual Create Project form; keeps the create payload, server field-error mapping and file attach. */
export default function CreateProject() {
  const navigate = useNavigate()
  const { me, org } = useOrg()
  const pm = usePmOptions()
  const createProject = useCreateProject(org.id)

  const [form, setForm] = useState<FormState>(() => initialProjectForm(me.id))
  const [errors, setErrors] = useState<FieldErrors>({})
  const [submitted, setSubmitted] = useState<Intent | null>(null)
  const [pendingIntent, setPendingIntent] = useState<Intent | null>(null)
  const busy = useRef(false)
  const [serverError, setServerError] = useState<{ title: string; detail: string } | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const banner = useBannerFocus()
  const attached = usePendingUploads()

  useEffect(() => {
    document.title = 'Create Project · NirnAIn'
  }, [])

  /**
   * Stores one field's value; after the first submit the form re-validates live so the banner shrinks as gaps
   * are fixed.
   */
  function update<TField extends FieldName>(key: TField, value: FormState[TField]) {
    const next = { ...form, [key]: value }
    setForm(next)
    // After the first click, re-validate live so the banner shrinks as gaps are fixed.
    if (submitted) setErrors(validateProjectForm(next, submitted))
  }

  /** Focuses a field from a banner or checklist link, opening "More details" first when needed. */
  function jump(key: string) {
    const name = key as FieldName
    if (IN_MORE_DETAILS.includes(name)) setMoreOpen(true)
    // After a possible re-render that opens "More details".
    window.setTimeout(() => {
      const element = document.getElementById(FIELD_IDS[name])
      element?.scrollIntoView({ block: 'center' })
      element?.focus()
    })
  }

  /** The shared props of a field's control: id, name, invalid outline and error description. */
  function bind(name: FieldName) {
    const id = FIELD_IDS[name]
    return {
      id,
      name,
      className: errors[name] ? 'in invalid' : 'in',
      'aria-invalid': errors[name] ? true : undefined,
      'aria-describedby': errors[name] ? `${id}-err` : undefined,
    }
  }

  /**
   * Validates for the intent, creates the project, uploads attached files and moves on; server field errors
   * go back on the form.
   */
  async function submit(intent: Intent) {
    if (busy.current) return
    setSubmitted(intent)
    setServerError(null)
    const found = validateProjectForm(form, intent)
    setErrors(found)
    if (Object.keys(found).length > 0) {
      banner.request()
      return
    }

    const payload = toProjectCreate(form, intent)

    busy.current = true
    setPendingIntent(intent)
    try {
      const project = await createProject.mutateAsync(payload)
      const uploadError = await attached.uploadTo(project.id)
      if (intent === 'draft') {
        navigate('/dashboard', { state: { notice: uploadError ?? `Draft “${project.name}” saved.` } })
      } else {
        navigate(`/projects/${encodeURIComponent(project.id)}/intake`, { state: { uploadError } })
      }
    } catch (error) {
      busy.current = false
      setPendingIntent(null)
      if (isApiError(error)) {
        const fieldErrors = serverFieldErrors(error.fieldErrors)
        if (Object.keys(fieldErrors).length > 0) setErrors((previous) => ({ ...previous, ...fieldErrors }))
        setServerError({
          title: error.status === 0 ? 'Connection problem' : "We couldn't create the project",
          detail: error.detail || error.title,
        })
      } else {
        setServerError({ title: "We couldn't create the project", detail: errorMessage(error) })
      }
      banner.request()
    }
  }

  /** Submitting the form (Enter) means Continue. */
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit('continue')
  }

  const checklist = validateProjectForm(form, 'continue')
  const left = REQUIRED_FIELDS.filter((field) => checklist[field]).length
  const problems = submitted ? FIELD_ORDER.filter((field) => errors[field]) : []
  const noun = problems.every((field) => REQUIRED_FIELDS.includes(field)) ? 'required field' : 'field'

  return (
    <section className="view on" id="v-create-form">
      <div className="pg">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; <Link to="/projects/new">Create Project</Link> &rsaquo; Enter the details
        </div>
        <h1>Create New Project</h1>
        <div className="sub" style={{ marginBottom: 18 }}>
          Four short sections. Only the first two are required. AI uses this context during intake.
        </div>

        <GapBanner
          id="cf-banner"
          tone="bad"
          ref={banner.ref}
          error={serverError}
          lead={`${problems.length === 1 ? `1 ${noun} needs` : `${problems.length} ${noun}s need`} your attention before you can ${submitted === 'draft' ? 'save a draft' : 'continue'}:`}
          items={problems.map((field) => ({ key: field, label: FIELD_LABELS[field], message: errors[field] ?? '' }))}
          onJump={jump}
        />

        <form onSubmit={onSubmit} noValidate aria-label="Create project">
          <div className="cf-layout">
            <div className="cf-main">
              <section className="card cf-sec" aria-labelledby="cf-sec-1">
                <SectionHead number={1} id="cf-sec-1" title="About the project" help="What it is and who it is for." required />
                <Field id={FIELD_IDS.name} label="Project name" required error={errors.name}>
                  <input
                    {...bind('name')}
                    value={form.name}
                    onChange={(event) => update('name', event.target.value)}
                    placeholder="e.g. Enterprise Data Platform"
                    maxLength={200}
                    autoComplete="off"
                    aria-required="true"
                  />
                </Field>
                <Field id={FIELD_IDS.description} label="Description" required error={errors.description}>
                  <textarea
                    {...bind('description')}
                    rows={3}
                    value={form.description}
                    onChange={(event) => update('description', event.target.value)}
                    placeholder="What is this project meant to achieve, and for whom?"
                    aria-required="true"
                  />
                </Field>
                <Field id={FIELD_IDS.type} label="Project type" error={errors.type}>
                  <OptionsSelect {...bind('type')} options={PROJECT_TYPES} labels={PROJECT_TYPE_LABELS} value={form.type} onValue={(value) => update('type', value)} />
                </Field>
              </section>

              <section className="card cf-sec" aria-labelledby="cf-sec-2">
                <SectionHead
                  number={2}
                  id="cf-sec-2"
                  title="Timeline & budget"
                  help="The optimizer plans inside these limits. You can refine them on step 5."
                  required
                />
                <div className="g3">
                  <Field id={FIELD_IDS.startDate} label="Start date" required error={errors.startDate}>
                    <input {...bind('startDate')} type="date" value={form.startDate} onChange={(event) => update('startDate', event.target.value)} aria-required="true" />
                  </Field>
                  <Field id={FIELD_IDS.targetEndDate} label="Target end date" required error={errors.targetEndDate}>
                    <input
                      {...bind('targetEndDate')}
                      type="date"
                      value={form.targetEndDate}
                      min={form.startDate || undefined}
                      onChange={(event) => update('targetEndDate', event.target.value)}
                      aria-required="true"
                    />
                  </Field>
                  <Field id={FIELD_IDS.budget} label="Total budget ($)" required error={errors.budget}>
                    <input
                      {...bind('budget')}
                      inputMode="decimal"
                      value={form.budget}
                      placeholder="e.g. 375,000"
                      autoComplete="off"
                      onChange={(event) => update('budget', event.target.value)}
                      onBlur={() => {
                        const cents = parseBudgetToCents(form.budget)
                        if (cents !== null && cents > 0) update('budget', formatBudgetInput(cents))
                      }}
                      aria-required="true"
                    />
                  </Field>
                </div>
              </section>

              <section className="card cf-sec" aria-labelledby="cf-sec-3">
                <SectionHead number={3} id="cf-sec-3" title="People" help="Who owns it. You add the delivery team on step 5." />
                <div className="g3">
                  <Field
                    id={FIELD_IDS.pmUserId}
                    label="Project manager"
                    error={errors.pmUserId}
                    hint={pm.failed ? "Couldn't load your team — you can still assign yourself." : undefined}
                  >
                    <select {...bind('pmUserId')} value={form.pmUserId} onChange={(event) => update('pmUserId', event.target.value)} aria-busy={pm.loading || undefined}>
                      {pm.options.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                      {pm.loading && (
                        <option disabled value="__loading">
                          Loading team…
                        </option>
                      )}
                    </select>
                  </Field>
                  <Field id={FIELD_IDS.department} label="Department" error={errors.department}>
                    <OptionsSelect {...bind('department')} options={DEPARTMENTS} value={form.department} onValue={(value) => update('department', value)} />
                  </Field>
                  <Field id={FIELD_IDS.sponsor} label="Sponsor" error={errors.sponsor}>
                    <input
                      {...bind('sponsor')}
                      value={form.sponsor}
                      onChange={(event) => update('sponsor', event.target.value)}
                      placeholder="e.g. VP of Engineering, Robert Walsh"
                      maxLength={200}
                      autoComplete="off"
                    />
                  </Field>
                </div>
              </section>

              <details className="card cf-sec cf-more" open={moreOpen} onToggle={(event) => setMoreOpen(event.currentTarget.open)}>
                <summary className="cf-sec-head">
                  <SectionHeadBody number={4} title="More details" help="Priority, methodology, visibility and supporting files. Defaults are fine for most projects." />
                </summary>
                <div className="g3 mt">
                  <Field id={FIELD_IDS.priority} label="Priority" error={errors.priority}>
                    <OptionsSelect {...bind('priority')} options={PRIORITIES} labels={PRIORITY_LABELS} value={form.priority} onValue={(value) => update('priority', value)} />
                  </Field>
                  <Field id={FIELD_IDS.methodology} label="Methodology" error={errors.methodology}>
                    <OptionsSelect {...bind('methodology')} options={METHODOLOGIES} labels={METHODOLOGY_LABELS} value={form.methodology} onValue={(value) => update('methodology', value)} />
                  </Field>
                  <Field id={FIELD_IDS.visibility} label="Visibility" error={errors.visibility}>
                    <OptionsSelect {...bind('visibility')} options={VISIBILITIES} labels={VISIBILITY_LABELS} value={form.visibility} onValue={(value) => update('visibility', value)} />
                  </Field>
                </div>
                <div className="sl mt">Supporting documents</div>
                <DocumentUpload
                  entries={attached.entries}
                  onFiles={attached.add}
                  onRemove={attached.remove}
                  disabled={pendingIntent !== null}
                  hint="Optional. Attach briefs, specs or meeting transcripts now; AI reads them in the AI Intake step. Max 100 MB per file."
                  error={attached.error}
                  idPrefix="cf"
                />
              </details>
            </div>

            <aside className="cf-rail" aria-label="Required to continue">
              <RequiredChecklist items={REQUIRED_FIELDS.map((field) => ({ key: field, label: FIELD_LABELS[field], problem: checklist[field] }))} onJump={jump} />
              <div className="card small muted">
                Have meeting notes?{' '}
                <button className="linkbtn" type="button" onClick={() => navigate('/projects/new/upload')}>
                  Start from a transcript instead
                </button>{' '}
                and AI fills these in for you to check.
              </div>
            </aside>
          </div>

          <div className="cf-actions">
            <Link className="btn sec" to="/projects/new">
              &larr; Back
            </Link>
            <div className="row">
              <StatusChip id="cf-status" todo={left ? `${left} required field${left > 1 ? 's' : ''} left` : null} ready="Ready to continue" />
              <button className="btn sec" id="btn-cf-draft" type="button" onClick={() => void submit('draft')} aria-busy={pendingIntent === 'draft' || undefined}>
                {pendingIntent === 'draft' ? 'Saving…' : 'Save Draft'}
              </button>
              <button className="btn" id="btn-cf-continue" type="submit" aria-busy={pendingIntent === 'continue' || undefined}>
                {pendingIntent === 'continue' ? (attached.uploading ? 'Uploading files…' : 'Creating…') : 'Continue to AI Intake →'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </section>
  )
}
