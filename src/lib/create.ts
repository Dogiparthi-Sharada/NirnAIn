/**
 * Data hooks and API types for step 1 (Create Project) and step 2 (AI Project Intake).
 *
 * - `usePmOptions`: project-manager choices for both create paths.
 * - Project drafts: `POST /orgs/{orgId}/project-drafts/extract` reads a file or pasted notes and
 *   returns each project value with its quote and status; nothing is saved (`useExtractDraft`).
 * - `useCreateFromDraft`: creates the project from a reviewed draft, keeps the file as a project
 *   document and starts the AI intake, degrading gracefully when either can't happen.
 * - `useIntakeRuns`: the "Previous Intakes" history.
 * Screens: `CreateProject`, `CreateFromUpload`, `AIProjectIntake` (and their components).
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { checkFile, type UploadEntry } from '@/components/DocumentUpload'
import { apiFetch, errorMessage, isApiError } from './api'
import { queryKeys, uploadDocument, useMembers } from './queries'
import { useOrg } from '@/org/useOrg'
import type { IntakeRun, Project, ProjectCreate, ScopeItem } from './types'

/** Project manager choices: org members, always including the current user (the default). */
export function usePmOptions() {
  const { me, org } = useOrg()
  const members = useMembers(org.id)
  const list = (members.data ?? []).map((member) => ({
    id: member.userId,
    label: member.fullName?.trim() || member.email || 'Unnamed member',
  }))
  if (!list.some((option) => option.id === me.id)) list.unshift({ id: me.id, label: me.fullName?.trim() || me.email || 'You' })
  return {
    options: list.map((option) => (option.id === me.id ? { ...option, label: `${option.label} (you)` } : option)),
    loading: members.isPending,
    failed: members.isError,
  }
}

/**
 * Files picked on the manual form before the project exists. They are checked on pick (type, 50 MB)
 * and uploaded as project documents right after the project is created (`uploadTo`).
 */
export function usePendingUploads() {
  const [files, setFiles] = useState<{ key: string; file: File }[]>([])
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)

  function add(picked: File[]) {
    setError(null)
    const accepted: { key: string; file: File }[] = []
    for (const file of picked) {
      const problem = checkFile(file)
      if (problem) setError(`${file.name}: ${problem}`)
      else accepted.push({ key: `${file.name}-${file.size}-${file.lastModified}`, file })
    }
    setFiles((previous) => [...previous, ...accepted.filter((candidate) => !previous.some((existing) => existing.key === candidate.key))])
  }

  /** Uploads every file to the new project; returns a message naming the ones that failed (never throws). */
  async function uploadTo(projectId: string): Promise<string | null> {
    if (files.length === 0) return null
    setUploading(true)
    const failed: string[] = []
    for (const { file } of files) {
      try {
        await uploadDocument(projectId, file)
      } catch (failure) {
        failed.push(`${file.name} (${errorMessage(failure)})`)
      }
    }
    setUploading(false)
    return failed.length > 0 ? `Some files couldn't be uploaded: ${failed.join('; ')}. Add them again below.` : null
  }

  const entries: UploadEntry[] = files.map(({ key, file }) => ({
    key,
    name: file.name,
    sizeBytes: file.size,
    status: uploading ? 'uploading' : 'pending',
    detail: uploading ? null : 'uploads when you continue',
  }))

  function remove(key: string) {
    setFiles((previous) => previous.filter((entry) => entry.key !== key))
  }

  return { entries, error, uploading, add, remove, uploadTo }
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

/** The server's limit on intake notes; pasted notes and fallback intake text are cut to it. */
export const MAX_NOTES_CHARS = 50_000

/** Reads a draft from one file or pasted notes; errors are RFC 7807 (413 / 415 audio / 422). */
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
  /** Notes pasted next to a file (empty when the pasted notes are the source); the intake reads them too. */
  notes: string
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
    mutationFn: async ({ orgId, project: body, draft, file, notes }: CreateFromDraftInput): Promise<CreateFromDraftResult> => {
      const project = await apiFetch<Project>(`/api/v1/orgs/${encodeURIComponent(orgId)}/projects`, { method: 'POST', json: body })
      queryClient.setQueryData(queryKeys.project(project.id), project)
      const problems: string[] = []
      let stored = false
      if (file) {
        try {
          const sourceDocument = await uploadDocument(project.id, file)
          stored = sourceDocument.extractionStatus === 'succeeded'
          if (!stored) problems.push(`${file.name} was saved, but its text couldn't be read${sourceDocument.extractionError ? ` (${sourceDocument.extractionError})` : ''}.`)
        } catch (error) {
          problems.push(`${file.name} couldn't be attached to the project (${isApiError(error) ? error.detail || error.title : 'upload failed'}).`)
        }
      }
      const pasted = notes.trim()
      const everything = [draft.text, pasted].filter(Boolean).join('\n\n').slice(0, MAX_NOTES_CHARS)
      try {
        const run = await startIntakeRun(project.id, stored ? pasted.slice(0, MAX_NOTES_CHARS) : everything).catch((error: unknown) => {
          // Servers that still require notes alongside a document: send the text we read.
          if (stored && isApiError(error) && error.status === 422) return startIntakeRun(project.id, everything)
          throw error
        })
        queryClient.setQueryData(queryKeys.latestIntakeRun(project.id), run)
      } catch (error) {
        problems.push(`AI intake couldn't start (${isApiError(error) ? error.detail || error.title : 'request failed'}). Run it again from AI Project Intake.`)
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

/** The source a project was created from in this browser, if any. */
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
