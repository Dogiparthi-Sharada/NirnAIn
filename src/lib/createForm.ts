/**
 * Rules of the manual Create Project form (step 1a, `screens/CreateProject.tsx`), kept apart from
 * the screen so the markup stays readable and Create from upload can share the budget rules.
 *
 * Validation mirrors the server's `ProjectCreate` (name 1–200 chars, end on/after start, budget as
 * integer cents) so problems show up inline before a round trip: "Save Draft" needs only a name,
 * "Continue" needs the five required fields. Money is integer cents on the wire.
 */
import { formatNumber, todayIsoDate } from './format'
import type { Methodology, Priority, ProjectCreate, ProjectType, Visibility } from './types'

/** Department choices of both create paths (the first is the default). */
export const DEPARTMENTS = ['Engineering', 'Operations', 'Finance', 'Product'] as const
const MAX_BUDGET_CENTS = 100_000_000_000_00 // $100B — guards against typos like an extra "000000".

/** Parses "375,000", "$375,000.50" or "375000" into integer cents; `null` if invalid. */
export function parseBudgetToCents(input: string): number | null {
  const cleaned = input.replace(/[\s,$]/g, '')
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned)
  if (!match) return null
  const whole = Number(match[1])
  const fraction = Number((match[2] ?? '').padEnd(2, '0'))
  const cents = whole * 100 + fraction
  return Number.isSafeInteger(cents) ? cents : null
}

/** Formats integer cents for the budget input, e.g. `37500000` → `375,000`. */
export function formatBudgetInput(cents: number): string {
  const whole = Math.floor(cents / 100)
  const fraction = cents % 100
  return fraction ? `${formatNumber(whole)}.${String(fraction).padStart(2, '0')}` : formatNumber(whole)
}

/** Budget format rules shared with Create from upload; `null` when the amount is fine. */
export function budgetProblem(input: string): string | null {
  const cents = parseBudgetToCents(input)
  if (cents === null) return 'Enter an amount like 375,000 or 375000.50.'
  if (cents <= 0) return 'Budget must be greater than zero.'
  if (cents > MAX_BUDGET_CENTS) return 'That budget looks too large — check for extra zeros.'
  return null
}

const CURRENCY = 'USD'

/** The form's values as typed (budget is the raw input text; dates `YYYY-MM-DD`). */
export interface FormState {
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

export type FieldName = keyof FormState
export type FieldErrors = Partial<Record<FieldName, string>>
/** Save Draft (status draft) or Continue to AI Intake (status planning). */
export type Intent = 'draft' | 'continue'

/** Element ids (as in the approved design); banner and checklist links jump to them. */
export const FIELD_IDS: Record<FieldName, string> = {
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

export const FIELD_LABELS: Record<FieldName, string> = {
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
export const FIELD_ORDER: FieldName[] = [
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
export const REQUIRED_FIELDS: FieldName[] = ['name', 'description', 'startDate', 'targetEndDate', 'budget']
/** Fields inside the collapsed "More details" section (opened before jumping to them). */
export const IN_MORE_DETAILS: FieldName[] = ['priority', 'methodology', 'visibility']

/** Problems per field. A draft only needs a name; anything else filled in must still be valid. */
export function validateProjectForm(form: FormState, intent: Intent): FieldErrors {
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

/** Maps the server's 422 field errors (camelCase API names) onto form fields. */
export function serverFieldErrors(fieldErrors: Record<string, string>): FieldErrors {
  const out: FieldErrors = {}
  for (const [field, message] of Object.entries(fieldErrors)) {
    const key = field === 'budgetCents' ? 'budget' : field
    if (key in FIELD_IDS) out[key as FieldName] = message
  }
  return out
}

/** A fresh form: today as the start date, the current user as PM, the usual defaults. */
export function initialProjectForm(currentUserId: string): FormState {
  return {
    name: '',
    type: 'technology',
    description: '',
    startDate: todayIsoDate(),
    targetEndDate: '',
    budget: '',
    pmUserId: currentUserId,
    department: DEPARTMENTS[0],
    sponsor: '',
    priority: 'medium',
    methodology: 'waterfall',
    visibility: 'internal',
  }
}

/** The create payload: drafts stay on step 'create', Continue moves the project to 'intake'. */
export function toProjectCreate(form: FormState, intent: Intent): ProjectCreate {
  return {
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
}
