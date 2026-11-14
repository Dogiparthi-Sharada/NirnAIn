/**
 * Review rules for "Create from a transcript or document" (step 1b).
 *
 * The server reads a draft of the project (`POST /orgs/{orgId}/project-drafts/extract`, see
 * `lib/create.ts`); the PM then checks every value before anything is saved. This module holds the
 * pure rules behind that review, so the screen (`screens/CreateFromUpload.tsx`) and the form
 * (`components/DraftReviewForm.tsx`) stay presentational:
 * - what each value's provenance is now (stated / inferred / not found / confirmed / edited),
 * - which values still need the PM before Create (missing required values, invalid values, and
 *   inferred values nobody confirmed — AI never decides those on its own),
 * - how the reviewed values map onto the create-project payload.
 */
import type { DraftStatus, ProjectDraft } from './create'
import { budgetProblem, formatBudgetInput, parseBudgetToCents } from './createForm'
import { formatDate } from './format'
import type { ProjectCreate } from './types'

export const REVIEW_KEYS = ['name', 'description', 'startDate', 'targetEndDate', 'budget', 'department', 'sponsor'] as const
export type ReviewKey = (typeof REVIEW_KEYS)[number]

/** Same required fields as the manual form. */
const REQUIRED_KEYS: readonly ReviewKey[] = ['name', 'description', 'startDate', 'targetEndDate', 'budget']

export const REVIEW_LABELS: Record<ReviewKey, string> = {
  name: 'Project Name',
  description: 'Project Description',
  startDate: 'Start Date',
  targetEndDate: 'Target End Date',
  budget: 'Total Budget',
  department: 'Department',
  sponsor: 'Sponsor',
}

/** Element ids in the review form (as in the approved design); banner links jump to them. */
export const REVIEW_IDS: Record<ReviewKey, string> = {
  name: 'pf-name',
  description: 'pf-desc',
  startDate: 'pf-start',
  targetEndDate: 'pf-end',
  budget: 'pf-budget',
  department: 'pf-dept',
  sponsor: 'pf-sponsor',
}

const MISSING_ACTION: Record<ReviewKey, string> = {
  name: 'Give the project a name.',
  description: 'Describe what the project will achieve.',
  startDate: 'Pick a date.',
  targetEndDate: 'Pick a date.',
  budget: 'Enter the total budget.',
  department: 'Pick a department.',
  sponsor: 'Enter the sponsor.',
}

const NAME_LIMIT = 200
const QUOTE_PREVIEW = 120

/** One reviewed value: what the source said (`origin`, `quote`, `original`) and what the PM has now. */
export interface ReviewValue {
  /** As shown in the input: dates `YYYY-MM-DD`, budget like `375,000`. */
  value: string
  original: string
  origin: DraftStatus
  quote: string | null
  /** The PM clicked "Confirm" on an inferred value. */
  confirmed: boolean
}

export type ReviewState = Record<ReviewKey, ReviewValue>

/** Provenance as shown on the field's chip. */
export type Mark = 'stated' | 'inferred' | 'missing' | 'default' | 'confirmed' | 'set' | 'edited'

export type ReviewIntent = 'create' | 'draft'

export interface ReviewNeed {
  key: ReviewKey
  message: string
}

/** The review form's starting values; a department the source doesn't name falls back to `defaultDepartment`. */
export function initialReview(draft: ProjectDraft, defaultDepartment: string): ReviewState {
  const fields = draft.fields
  const entry = (origin: DraftStatus, quote: string | null, value: string): ReviewValue => ({
    value,
    original: value,
    origin,
    quote,
    confirmed: false,
  })
  return {
    name: entry(fields.name.status, fields.name.quote, fields.name.value ?? ''),
    description: entry(fields.description.status, fields.description.quote, fields.description.value ?? ''),
    startDate: entry(fields.startDate.status, fields.startDate.quote, fields.startDate.value ?? ''),
    targetEndDate: entry(fields.targetEndDate.status, fields.targetEndDate.quote, fields.targetEndDate.value ?? ''),
    budget: entry(fields.budgetCents.status, fields.budgetCents.quote, fields.budgetCents.value != null ? formatBudgetInput(fields.budgetCents.value) : ''),
    department: entry(fields.department.status, fields.department.quote, fields.department.value ?? defaultDepartment),
    sponsor: entry(fields.sponsor.status, fields.sponsor.quote, fields.sponsor.value ?? ''),
  }
}

/** Where the value in the field comes from now: the PM's own input wins over what the source said. */
export function markOf(item: ReviewValue): Mark {
  if (!item.value.trim()) return 'missing'
  if (item.value !== item.original) return item.original ? 'edited' : 'set'
  if (item.confirmed) return 'confirmed'
  if (item.origin === 'missing') return 'default'
  return item.origin
}

/**
 * What "Confirm …" names: the date or amount itself ("Confirm Sep 30, 2025"); null for text, whose
 * value is already in the field right above.
 */
export function confirmTarget(key: ReviewKey, value: string): string | null {
  if (key === 'startDate' || key === 'targetEndDate') return formatDate(value)
  if (key === 'budget') return `$${value}`
  return null
}

function preview(quote: string): string {
  return quote.length > QUOTE_PREVIEW ? `${quote.slice(0, QUOTE_PREVIEW - 1)}…` : quote
}

function problemWith(state: ReviewState, key: ReviewKey, intent: ReviewIntent): string | null {
  const item = state[key]
  const value = item.value.trim()
  if (!value) {
    const required = key === 'name' || (intent === 'create' && REQUIRED_KEYS.includes(key))
    if (!required) return null
    return (item.origin === 'missing' ? 'Not in your notes. ' : '') + MISSING_ACTION[key]
  }
  if (key === 'name' && value.length > NAME_LIMIT) return 'Keep the name under 200 characters.'
  if (key === 'targetEndDate' && state.startDate.value && value < state.startDate.value) return 'End date must be on or after the start date.'
  if (key === 'budget') return budgetProblem(value)
  return null
}

/**
 * What still needs the PM, in form order. A draft only needs a name and valid values; Create also
 * needs every required value and a decision on each inferred value (confirm it or change it).
 */
export function reviewNeeds(state: ReviewState, intent: ReviewIntent): ReviewNeed[] {
  const needs: ReviewNeed[] = []
  for (const key of REVIEW_KEYS) {
    const item = state[key]
    const problem = problemWith(state, key, intent)
    if (problem) {
      needs.push({ key, message: problem })
    } else if (intent === 'create' && markOf(item) === 'inferred') {
      const value = confirmTarget(key, item.value) ?? 'it'
      needs.push({
        key,
        message: item.quote
          ? `Inferred from “${preview(item.quote)}”. Confirm ${value} or change it.`
          : `Inferred, not quoted from your notes. Confirm ${value} or change it.`,
      })
    }
  }
  return needs
}

/** The reviewed values as create-project fields (call only once `reviewNeeds` is empty for the intent). */
export function reviewedFields(
  state: ReviewState,
): Pick<ProjectCreate, 'name' | 'description' | 'startDate' | 'targetEndDate' | 'budgetCents' | 'department' | 'sponsor'> {
  const text = (key: ReviewKey) => state[key].value.trim()
  return {
    name: text('name'),
    description: text('description'),
    startDate: text('startDate') || null,
    targetEndDate: text('targetEndDate') || null,
    budgetCents: text('budget') ? parseBudgetToCents(text('budget')) : null,
    department: text('department') || null,
    sponsor: text('sponsor') || null,
  }
}
