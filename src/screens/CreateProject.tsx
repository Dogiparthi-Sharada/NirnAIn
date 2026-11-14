import { useEffect, useRef, useState, type FormEvent, type MouseEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useOrg } from '@/org/useOrg'
import { uploadDocument, useCreateProject, useMembers } from '@/lib/queries'
import { errorMessage, isApiError } from '@/lib/api'
import { todayIsoDate } from '@/lib/format'
import { budgetProblem, DEPARTMENTS, formatBudgetInput, parseBudgetToCents } from '@/lib/create'
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
  type ProjectCreate,
  type ProjectType,
  type Visibility,
} from '@/lib/types'
import DocumentUpload, { checkFile, type UploadEntry } from '@/components/DocumentUpload'
import { Field, SectionHead, SectionHeadBody } from '@/components/CreateForm'

const CURRENCY = 'USD'

interface FormState {
  name: string
  type: ProjectType
  description: string
  startDate: string
  targetEndDate: string
  budget: string
  pmUserId: string
  department: string
  sponsor: string
  priority: Priority
  methodology: Methodology
  visibility: Visibility
}

type FieldName = keyof FormState
type FieldErrors = Partial<Record<FieldName, string>>
type Intent = 'draft' | 'continue'

/** Element ids (as in the approved design); banner and checklist links jump to them. */
const FIELD_IDS: Record<FieldName, string> = {
  name: 'cf-name',
  description: 'cf-desc',
  type: 'cf-type',
  startDate: 'cf-start',
  targetEndDate: 'cf-end',
  budget: 'cf-budget',
  pmUserId: 'cf-pm',
  department: 'cf-dept',
  sponsor: 'cf-sponsor',
  priority: 'cf-prio',
  methodology: 'cf-meth',
  visibility: 'cf-vis',
}

const FIELD_LABELS: Record<FieldName, string> = {
  name: 'Project name',
  description: 'Description',
  type: 'Project type',
  startDate: 'Start date',
  targetEndDate: 'Target end date',
  budget: 'Total budget',
  pmUserId: 'Project manager',
  department: 'Department',
  sponsor: 'Sponsor',
  priority: 'Priority',
  methodology: 'Methodology',
  visibility: 'Visibility',
}

/** Display order of the banner. */
const FIELD_ORDER: FieldName[] = [
  'name',
  'description',
  'type',
  'startDate',
  'targetEndDate',
  'budget',
  'pmUserId',
  'department',
  'sponsor',
  'priority',
  'methodology',
  'visibility',
]

/** What the "Required to continue" checklist counts. */
const REQUIRED: FieldName[] = ['name', 'description', 'startDate', 'targetEndDate', 'budget']
const IN_MORE_DETAILS: FieldName[] = ['priority', 'methodology', 'visibility']

/** A draft only needs a name; anything else filled in must still be valid. */
function validate(form: FormState, intent: Intent): FieldErrors {
  const errors: FieldErrors = {}
  const required = intent === 'continue'
  const name = form.name.trim()
  if (!name) errors.name = 'Give the project a name.'
  else if (name.length > 200) errors.name = 'Keep the name under 200 characters.'
  if (required && !form.description.trim()) errors.description = 'Describe the project so AI has context during intake.'
  if (required && !form.startDate) errors.startDate = 'Choose a start date.'
  if (!form.targetEndDate) {
    if (required) errors.targetEndDate = 'Choose a target end date.'
  } else if (form.startDate && form.targetEndDate < form.startDate) {
    errors.targetEndDate = 'End date must be on or after the start date.'
  }
  if (!form.budget.trim()) {
    if (required) errors.budget = 'Enter the total budget.'
  } else {
    const problem = budgetProblem(form.budget)
    if (problem) errors.budget = problem
  }
  return errors
}

/** Maps server field names (camelCase API) onto form fields. */
function serverFieldErrors(fieldErrors: Record<string, string>): FieldErrors {
  const out: FieldErrors = {}
  for (const [field, message] of Object.entries(fieldErrors)) {
    const key = field === 'budgetCents' ? 'budget' : field
    if (key in FIELD_IDS) out[key as FieldName] = message
  }
  return out
}

/** Step 1a, Create Project by hand (mockup create-form.js): four short sections, a checklist and a gap banner. */
export default function CreateProject() {
  const navigate = useNavigate()
  const { me, org } = useOrg()
  const members = useMembers(org.id)
  const createProject = useCreateProject(org.id)

  const [form, setForm] = useState<FormState>(() => ({
    name: '',
    type: 'technology',
    description: '',
    startDate: todayIsoDate(),
    targetEndDate: '',
    budget: '',
    pmUserId: me.id,
    department: DEPARTMENTS[0],
    sponsor: '',
    priority: 'medium',
    methodology: 'waterfall',
    visibility: 'internal',
  }))
  const [errors, setErrors] = useState<FieldErrors>({})
  const [submitted, setSubmitted] = useState<Intent | null>(null)
  const [pendingIntent, setPendingIntent] = useState<Intent | null>(null)
  const busy = useRef(false)
  const [serverError, setServerError] = useState<{ title: string; detail: string } | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const bannerRef = useRef<HTMLDivElement>(null)
  const [bannerFocus, setBannerFocus] = useState(0)
  // Files picked before the project exists; uploaded right after it is created.
  const [files, setFiles] = useState<{ key: string; file: File }[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploadingCount, setUploadingCount] = useState(0)

  useEffect(() => {
    document.title = 'Create Project · NirnAIn'
  }, [])

  // Focus moves to the banner after it has rendered, so screen readers announce it.
  useEffect(() => {
    if (bannerFocus === 0) return
    bannerRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    bannerRef.current?.focus({ preventScroll: true })
  }, [bannerFocus])

  // PM candidates: org members, always including the current user as the default.
  const pmOptions = (() => {
    const list = (members.data ?? []).map((m) => ({ id: m.userId, label: m.fullName?.trim() || m.email || 'Unnamed member' }))
    if (!list.some((m) => m.id === me.id)) list.unshift({ id: me.id, label: me.fullName?.trim() || me.email || 'You' })
    return list.map((m) => (m.id === me.id ? { ...m, label: `${m.label} (you)` } : m))
  })()

  function update<K extends FieldName>(key: K, value: FormState[K]) {
    const next = { ...form, [key]: value }
    setForm(next)
    // After the first click, re-validate live so the banner shrinks as gaps are fixed.
    if (submitted) setErrors(validate(next, submitted))
  }

  function jump(name: FieldName) {
    if (IN_MORE_DETAILS.includes(name)) setMoreOpen(true)
    // After a possible re-render that opens "More details".
    window.setTimeout(() => {
      const el = document.getElementById(FIELD_IDS[name])
      el?.scrollIntoView({ block: 'center' })
      el?.focus()
    })
  }

  function jumpLink(name: FieldName) {
    return {
      href: `#${FIELD_IDS[name]}`,
      'data-jump': FIELD_IDS[name].slice(3),
      onClick: (event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault()
        jump(name)
      },
    }
  }

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

  function addFiles(picked: File[]) {
    setFileError(null)
    const accepted: { key: string; file: File }[] = []
    for (const file of picked) {
      const problem = checkFile(file)
      if (problem) setFileError(`${file.name}: ${problem}`)
      else accepted.push({ key: `${file.name}-${file.size}-${file.lastModified}`, file })
    }
    setFiles((prev) => [...prev, ...accepted.filter((a) => !prev.some((p) => p.key === a.key))])
  }

  /** Uploads the attached files to the new project; returns a message when some failed. */
  async function uploadAttached(projectId: string): Promise<string | null> {
    if (files.length === 0) return null
    setUploadingCount(files.length)
    const failed: string[] = []
    for (const { file } of files) {
      try {
        await uploadDocument(projectId, file)
      } catch (error) {
        failed.push(`${file.name} (${errorMessage(error)})`)
      }
    }
    setUploadingCount(0)
    return failed.length > 0 ? `Some files couldn't be uploaded: ${failed.join('; ')}. Add them again below.` : null
  }

  async function submit(intent: Intent) {
    if (busy.current) return
    setSubmitted(intent)
    setServerError(null)
    const found = validate(form, intent)
    setErrors(found)
    if (Object.keys(found).length > 0) {
      setBannerFocus((n) => n + 1)
      return
    }

    const payload: ProjectCreate = {
      name: form.name.trim(),
      description: form.description.trim(),
      type: form.type,
      department: form.department || null,
      sponsor: form.sponsor.trim() || null,
      pmUserId: form.pmUserId || null,
      priority: form.priority,
      methodology: form.methodology,
      visibility: form.visibility,
      startDate: form.startDate || null,
      targetEndDate: form.targetEndDate || null,
      budgetCents: form.budget.trim() ? parseBudgetToCents(form.budget) : null,
      currency: CURRENCY,
      status: intent === 'draft' ? 'draft' : 'planning',
      currentStep: intent === 'draft' ? 'create' : 'intake',
    }

    busy.current = true
    setPendingIntent(intent)
    try {
      const project = await createProject.mutateAsync(payload)
      const uploadError = await uploadAttached(project.id)
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
        if (Object.keys(fieldErrors).length > 0) setErrors((prev) => ({ ...prev, ...fieldErrors }))
        setServerError({
          title: error.status === 0 ? 'Connection problem' : "We couldn't create the project",
          detail: error.detail || error.title,
        })
      } else {
        setServerError({ title: "We couldn't create the project", detail: errorMessage(error) })
      }
      setBannerFocus((n) => n + 1)
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit('continue')
  }

  const checklist = validate(form, 'continue')
  const done = REQUIRED.filter((k) => !checklist[k]).length
  const left = REQUIRED.length - done
  const problems = submitted ? FIELD_ORDER.filter((k) => errors[k]) : []
  const noun = problems.every((k) => REQUIRED.includes(k)) ? 'required field' : 'field'

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

        <div className="cf-banner bad" id="cf-banner" role="alert" tabIndex={-1} ref={bannerRef} hidden={problems.length === 0 && !serverError}>
          {serverError && (
            <>
              <b>&#9888; {serverError.title}.</b> {serverError.detail}
            </>
          )}
          {problems.length > 0 && (
            <>
              {serverError && <br />}
              <b>
                &#9888; {problems.length === 1 ? `1 ${noun} needs` : `${problems.length} ${noun}s need`} your attention before you can{' '}
                {submitted === 'draft' ? 'save a draft' : 'continue'}:
              </b>
              <ul>
                {problems.map((k) => (
                  <li key={k}>
                    <a {...jumpLink(k)}>{FIELD_LABELS[k]}</a>: {errors[k]}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <form onSubmit={onSubmit} noValidate aria-label="Create project">
          <div className="cf-layout">
            <div className="cf-main">
              <section className="card cf-sec" aria-labelledby="cf-sec-1">
                <SectionHead n={1} id="cf-sec-1" title="About the project" help="What it is and who it is for." required />
                <Field id={FIELD_IDS.name} label="Project name" required error={errors.name}>
                  <input
                    {...bind('name')}
                    value={form.name}
                    onChange={(e) => update('name', e.target.value)}
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
                    onChange={(e) => update('description', e.target.value)}
                    placeholder="What is this project meant to achieve, and for whom?"
                    aria-required="true"
                  />
                </Field>
                <Field id={FIELD_IDS.type} label="Project type" error={errors.type}>
                  <select {...bind('type')} value={form.type} onChange={(e) => update('type', e.target.value as ProjectType)}>
                    {PROJECT_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {PROJECT_TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                </Field>
              </section>

              <section className="card cf-sec" aria-labelledby="cf-sec-2">
                <SectionHead
                  n={2}
                  id="cf-sec-2"
                  title="Timeline & budget"
                  help="The optimizer plans inside these limits. You can refine them on step 5."
                  required
                />
                <div className="g3">
                  <Field id={FIELD_IDS.startDate} label="Start date" required error={errors.startDate}>
                    <input {...bind('startDate')} type="date" value={form.startDate} onChange={(e) => update('startDate', e.target.value)} aria-required="true" />
                  </Field>
                  <Field id={FIELD_IDS.targetEndDate} label="Target end date" required error={errors.targetEndDate}>
                    <input
                      {...bind('targetEndDate')}
                      type="date"
                      value={form.targetEndDate}
                      min={form.startDate || undefined}
                      onChange={(e) => update('targetEndDate', e.target.value)}
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
                      onChange={(e) => update('budget', e.target.value)}
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
                <SectionHead n={3} id="cf-sec-3" title="People" help="Who owns it. You add the delivery team on step 5." />
                <div className="g3">
                  <Field
                    id={FIELD_IDS.pmUserId}
                    label="Project manager"
                    error={errors.pmUserId}
                    hint={members.isError ? "Couldn't load your team — you can still assign yourself." : undefined}
                  >
                    <select {...bind('pmUserId')} value={form.pmUserId} onChange={(e) => update('pmUserId', e.target.value)} aria-busy={members.isPending || undefined}>
                      {pmOptions.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label}
                        </option>
                      ))}
                      {members.isPending && (
                        <option disabled value="__loading">
                          Loading team…
                        </option>
                      )}
                    </select>
                  </Field>
                  <Field id={FIELD_IDS.department} label="Department" error={errors.department}>
                    <select {...bind('department')} value={form.department} onChange={(e) => update('department', e.target.value)}>
                      {DEPARTMENTS.map((d) => (
                        <option key={d} value={d}>
                          {d}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field id={FIELD_IDS.sponsor} label="Sponsor" error={errors.sponsor}>
                    <input
                      {...bind('sponsor')}
                      value={form.sponsor}
                      onChange={(e) => update('sponsor', e.target.value)}
                      placeholder="e.g. VP of Engineering, Robert Walsh"
                      maxLength={200}
                      autoComplete="off"
                    />
                  </Field>
                </div>
              </section>

              <details className="card cf-sec cf-more" open={moreOpen} onToggle={(e) => setMoreOpen(e.currentTarget.open)}>
                <summary className="cf-sec-head">
                  <SectionHeadBody n={4} title="More details" help="Priority, methodology, visibility and supporting files. Defaults are fine for most projects." />
                </summary>
                <div className="g3 mt">
                  <Field id={FIELD_IDS.priority} label="Priority" error={errors.priority}>
                    <select {...bind('priority')} value={form.priority} onChange={(e) => update('priority', e.target.value as Priority)}>
                      {PRIORITIES.map((p) => (
                        <option key={p} value={p}>
                          {PRIORITY_LABELS[p]}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field id={FIELD_IDS.methodology} label="Methodology" error={errors.methodology}>
                    <select {...bind('methodology')} value={form.methodology} onChange={(e) => update('methodology', e.target.value as Methodology)}>
                      {METHODOLOGIES.map((m) => (
                        <option key={m} value={m}>
                          {METHODOLOGY_LABELS[m]}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field id={FIELD_IDS.visibility} label="Visibility" error={errors.visibility}>
                    <select {...bind('visibility')} value={form.visibility} onChange={(e) => update('visibility', e.target.value as Visibility)}>
                      {VISIBILITIES.map((v) => (
                        <option key={v} value={v}>
                          {VISIBILITY_LABELS[v]}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <div className="sl mt">Supporting documents</div>
                <DocumentUpload
                  entries={files.map<UploadEntry>(({ key, file }) => ({
                    key,
                    name: file.name,
                    sizeBytes: file.size,
                    status: uploadingCount > 0 ? 'uploading' : 'pending',
                    detail: uploadingCount > 0 ? null : 'uploads when you continue',
                  }))}
                  onFiles={addFiles}
                  onRemove={(key) => setFiles((prev) => prev.filter((f) => f.key !== key))}
                  disabled={pendingIntent !== null}
                  hint="Optional. Attach briefs, specs or meeting transcripts now; AI reads them in the AI Intake step. Max 50 MB per file."
                  error={fileError}
                  idPrefix="cf"
                />
              </details>
            </div>

            <aside className="cf-rail" aria-label="Required to continue">
              <div className="card cf-check">
                <div className="row between">
                  <b>Required to continue</b>
                  <span className="cf-count" id="cf-count">
                    {done} of {REQUIRED.length}
                  </span>
                </div>
                <div className="cf-meter" aria-hidden="true">
                  <i id="cf-meter" style={{ width: `${(done / REQUIRED.length) * 100}%` }} />
                </div>
                <ul className="cf-list" id="cf-list">
                  {REQUIRED.map((k) =>
                    checklist[k] ? (
                      <li key={k} className="bad">
                        <a {...jumpLink(k)}>{FIELD_LABELS[k]}</a>
                        <small>{checklist[k]}</small>
                      </li>
                    ) : (
                      <li key={k} className="ok">
                        {FIELD_LABELS[k]}
                      </li>
                    ),
                  )}
                </ul>
              </div>
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
              <span className={`cf-status ${left ? 'todo' : 'done'}`} id="cf-status" aria-live="polite">
                {left ? `${left} required field${left > 1 ? 's' : ''} left` : '✓ Ready to continue'}
              </span>
              <button className="btn sec" id="btn-cf-draft" type="button" onClick={() => void submit('draft')} aria-busy={pendingIntent === 'draft' || undefined}>
                {pendingIntent === 'draft' ? 'Saving…' : 'Save Draft'}
              </button>
              <button className="btn" id="btn-cf-continue" type="submit" aria-busy={pendingIntent === 'continue' || undefined}>
                {pendingIntent === 'continue' ? (uploadingCount > 0 ? 'Uploading files…' : 'Creating…') : 'Continue to AI Intake →'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </section>
  )
}
