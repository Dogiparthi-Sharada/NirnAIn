import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import { queryKeys, uploadDocument } from './queries'
import { formatNumber } from './format'
import type { IntakeRun, Project, ProjectCreate, ScopeItem } from './types'

/* ───────────────────────────── Create Project form rules ───────────────────────────── */

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


/* ───────────────────────────── Project drafts (create from upload) ───────────────────────────── */

/** Where a value read from the source came from. */
export type DraftStatus = 'stated' | 'inferred' | 'missing'

export interface DraftField<T> {
  value: T | null
  /** The sentence it came from, word for word (null when missing or not quotable). */
  quote: string | null
  status: DraftStatus
}

export interface ProjectDraftFields {
  name: DraftField<string>
  description: DraftField<string>
  /** ISO `YYYY-MM-DD`. */
  startDate: DraftField<string>
  /** ISO `YYYY-MM-DD`. */
  targetEndDate: DraftField<string>
  budgetCents: DraftField<number>
  department: DraftField<string>
  sponsor: DraftField<string>
}

/** `POST /orgs/{orgId}/project-drafts/extract`: nothing is saved. */
export interface ProjectDraft {
  fields: ProjectDraftFields
  /** The text that was read (the file's text or the pasted notes). */
  text: string
  sourceName: string
  /** Claude model id, or `'rules'` when the server read it without AI. */
  model: string
}

export type DraftSource = { file: File } | { text: string }

export const MAX_NOTES_CHARS = 50_000

export function useExtractDraft(orgId: string) {
  return useMutation({
    mutationFn: (source: DraftSource) => {
      const path = `/api/v1/orgs/${encodeURIComponent(orgId)}/project-drafts/extract`
      if ('file' in source) {
        const body = new FormData()
        body.append('file', source.file, source.file.name)
        return apiFetch<ProjectDraft>(path, { method: 'POST', body })
      }
      return apiFetch<ProjectDraft>(path, { method: 'POST', json: { text: source.text } })
    },
  })
}

function startIntakeRun(projectId: string, text: string): Promise<IntakeRun> {
  return apiFetch<IntakeRun>(`/api/v1/projects/${encodeURIComponent(projectId)}/intake/runs`, {
    method: 'POST',
    json: { text },
  })
}

export interface CreateFromDraftInput {
  orgId: string
  project: ProjectCreate
  draft: ProjectDraft
  /** The uploaded file, kept with the project as a source document. */
  file: File | null
  /** Drafts are saved without running the intake. */
  runIntake: boolean
}

export interface CreateFromDraftResult {
  project: Project
  /** Set when the file upload or the intake couldn't start; the project exists either way. */
  problem: string | null
}

/**
 * Create from upload: creates the project, keeps the file as a project document and starts the AI
 * intake on it (or on the pasted notes). When the file can't be stored, the intake reads the text
 * extracted for the draft instead, so the scope is never lost.
 */
export function useCreateFromDraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ orgId, project: body, draft, file, runIntake }: CreateFromDraftInput): Promise<CreateFromDraftResult> => {
      const project = await apiFetch<Project>(`/api/v1/orgs/${encodeURIComponent(orgId)}/projects`, { method: 'POST', json: body })
      queryClient.setQueryData(queryKeys.project(project.id), project)
      const problems: string[] = []
      let stored = false
      if (file) {
        try {
          const doc = await uploadDocument(project.id, file)
          stored = doc.extractionStatus === 'succeeded'
          if (!stored) problems.push(`${file.name} was saved, but its text couldn't be read${doc.extractionError ? ` (${doc.extractionError})` : ''}.`)
        } catch (error) {
          problems.push(`${file.name} couldn't be attached to the project (${isApiError(error) ? error.detail || error.title : 'upload failed'}).`)
        }
      }
      if (runIntake) {
        const notes = draft.text.slice(0, MAX_NOTES_CHARS)
        try {
          const run = await startIntakeRun(project.id, stored ? '' : notes).catch((error: unknown) => {
            // Servers that still require notes alongside a document: send the text we read.
            if (stored && isApiError(error) && error.status === 422) return startIntakeRun(project.id, notes)
            throw error
          })
          queryClient.setQueryData(queryKeys.latestIntakeRun(project.id), run)
        } catch (error) {
          problems.push(`AI intake couldn't start (${isApiError(error) ? error.detail || error.title : 'request failed'}). Run it again from AI Project Intake.`)
        }
      }
      return { project, problem: problems.length > 0 ? problems.join(' ') : null }
    },
    onSettled: (_data, _error, { orgId }) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects(orgId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard(orgId) }),
      ]),
  })
}

/* ───────────────────────────── Where a project started ───────────────────────────── */

const SOURCE_KEY = 'nirnain:upload-source:'

/** Remembers (in this browser) that a project was created from an upload, for AI Project Intake's note. */
export function rememberUploadSource(projectId: string, sourceName: string): void {
  try {
    window.localStorage.setItem(SOURCE_KEY + projectId, sourceName)
  } catch {
    // Storage unavailable (private mode): the note is a nicety.
  }
}

export function uploadSource(projectId: string): string | null {
  try {
    return window.localStorage.getItem(SOURCE_KEY + projectId)
  } catch {
    return null
  }
}

/* ───────────────────────────── AI Project Intake ───────────────────────────── */

/** `GET /projects/{projectId}/intake/runs`, newest first ("Previous Intakes"). */
export function useIntakeRuns(projectId: string, refreshKey?: string) {
  return useQuery({
    // `refreshKey` (latest run id + status) refetches the list when a run starts or finishes.
    queryKey: ['projects', projectId, 'intake', 'runs', refreshKey ?? null] as const,
    queryFn: ({ signal }) => apiFetch<IntakeRun[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/intake/runs`, { signal }),
    enabled: Boolean(projectId),
    placeholderData: (previous) => previous,
  })
}

/** Scope evidence added by the intake (older servers send none). */
export type ScopeEvidence = 'stated' | 'inferred' | 'flagged'
export type ScopeItemWithEvidence = ScopeItem & { evidence?: ScopeEvidence | null }
