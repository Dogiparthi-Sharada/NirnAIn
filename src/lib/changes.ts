import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import { queryKeys } from './queries'
import type { OptimizationRun, RunStatus, TeamMember } from './types'

/* ───────────────────────────── Types (backend: app/domain/changes.py) ───────────────────────────── */

/** An applied plan: Plan v1, v2… (BE1 `GET /projects/{id}/plan-versions`, oldest first). */
export interface PlanVersion {
  version: number
  runId: string
  appliedAt: string | null
  completionDate: string | null
  expectedCostCents: number | null
  solver: string | null
  label: string | null
}

export type ChangeSource = 'notes' | 'edits' | 'progress'
export type ChangeKind = 'budget' | 'deadline' | 'duration' | 'capacity' | 'scope'
export type ChangeStatus = 'draft' | 'analyzing' | 'ready' | 'replanning' | 'proposed' | 'approved' | 'rejected' | 'failed'

export interface TargetOption {
  id: string
  label: string
  from: number | null
  to: number | null
}

/** budget = cents, deadline = ISO date, duration = working days, capacity = hours/week (0 = leaves), scope = text. */
export interface DetectedChange {
  id: string
  kind: ChangeKind
  target: string | null
  targetLabel: string | null
  from: number | string | null
  to: number | string | null
  quote: string | null
  speaker: string | null
  confidence: number
  note: string | null
  candidates: TargetOption[]
}

export interface ChangeQuestion {
  id: string
  kind: 'budget' | 'deadline' | 'duration' | 'capacity' | 'target'
  changeId: string
  tier: 'high' | 'blocking'
  text: string
  why: string | null
  options: { value: string; label: string }[]
}

export interface ChangeImpact {
  changes: DetectedChange[]
  questions: ChangeQuestion[]
  affects: {
    tasks: { id: string; code: string; name: string }[]
    people: { id: string; name: string }[]
    costCents: number | null
    completionDate: string | null
  }
  /** `rules` when the deterministic reader ran (no AI key), else the Claude model id. */
  model: string
}

/** Body of `overrides` on C1's `POST /projects/{id}/optimization/runs`. */
export interface PlanOverrides {
  budgetTotalCents?: number
  mustFinishBy?: string
  durationDays?: Record<string, number>
  hoursPerWeek?: Record<string, number>
}

export interface ChangeRequest {
  id: string
  projectId: string
  source: ChangeSource
  inputText: string | null
  status: ChangeStatus
  impact: ChangeImpact | null
  answers: Record<string, string>
  baseRunId: string | null
  baseVersion: number | null
  proposedRunId: string | null
  proposedRunStatus: RunStatus | null
  error: string | null
  createdAt: string
  decidedAt: string | null
  /** Set once every question is answered. */
  overrides: PlanOverrides | null
}

export interface ChangeEdits {
  budgetTotalCents?: number
  mustFinishBy?: string
  durationDays?: Record<string, number>
  hoursPerWeek?: Record<string, number>
  /** Percent complete per work package (100 = done). */
  progress?: Record<string, number>
}

export interface PlanBar {
  start: string
  end: string
  owner: string | null
}

export interface CompareRow {
  wbsNodeId: string
  code: string
  name: string
  groupCode: string | null
  groupName: string | null
  before: PlanBar | null
  after: PlanBar | null
  shiftDays: number | null
  ownerChanged: boolean
  why: string | null
}

export interface PlanSide {
  runId: string
  version: number | null
  status: RunStatus
  expectedCostCents: number | null
  completionDate: string | null
  budgetCents: number | null
  mustFinishBy: string | null
}

export interface ChangeComparison {
  base: PlanSide
  proposed: PlanSide
  rows: CompareRow[]
}

/** Who is on the project and the hours each gives it (B's team, else the org pool). */
export interface ChangePerson {
  employeeId: string
  fullName: string
  hoursPerWeek: number
}

/* ───────────────────────────── Hooks ───────────────────────────── */

const RUN_POLL_MS = 2000

const keys = {
  versions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
  changes: (projectId: string) => ['projects', projectId, 'changes'] as const,
  team: (projectId: string) => ['projects', projectId, 'changes', 'team'] as const,
  run: (projectId: string, runId: string) => ['projects', projectId, 'optimization', 'runs', runId] as const,
  compare: (changeId: string, runId: string) => ['changes', changeId, 'compare', runId] as const,
}

const project = (projectId: string, suffix: string) => `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`
const change = (changeId: string, suffix = '') => `/api/v1/changes/${encodeURIComponent(changeId)}${suffix}`

export function isRunning(status: RunStatus | null | undefined): boolean {
  return status === 'queued' || status === 'running'
}

export function isSolved(status: RunStatus | null | undefined): boolean {
  return status === 'optimal' || status === 'feasible'
}

export function usePlanVersions(projectId: string) {
  return useQuery({
    queryKey: keys.versions(projectId),
    queryFn: ({ signal }) => apiFetch<PlanVersion[]>(project(projectId, '/plan-versions'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useChanges(projectId: string, enabled = true) {
  return useQuery({
    queryKey: keys.changes(projectId),
    queryFn: ({ signal }) => apiFetch<ChangeRequest[]>(project(projectId, '/changes'), { signal }),
    enabled: Boolean(projectId) && enabled,
  })
}

/** The project's team (B), falling back to the org's capacity list when the team is empty. */
export function useChangeTeam(projectId: string, enabled = true) {
  return useQuery({
    queryKey: keys.team(projectId),
    queryFn: async ({ signal }): Promise<ChangePerson[]> => {
      try {
        const team = await apiFetch<{ employeeId: string; fullName: string; hoursPerWeek: number }[]>(
          project(projectId, '/team'),
          { signal },
        )
        if (team.length > 0) return team.map(({ employeeId, fullName, hoursPerWeek }) => ({ employeeId, fullName, hoursPerWeek }))
      } catch (error) {
        if (!isApiError(error) || error.status !== 404) throw error
      }
      const pool = await apiFetch<TeamMember[]>(project(projectId, '/capacity'), { signal })
      return pool.map((member) => ({ employeeId: member.employeeId, fullName: member.fullName, hoursPerWeek: member.weeklyCapacityHours }))
    },
    enabled: Boolean(projectId) && enabled,
  })
}

function upsert(list: ChangeRequest[] | undefined, item: ChangeRequest): ChangeRequest[] {
  if (!list) return [item]
  return list.some((entry) => entry.id === item.id) ? list.map((entry) => (entry.id === item.id ? item : entry)) : [item, ...list]
}

function useStoreChange(projectId: string) {
  const queryClient = useQueryClient()
  return (item: ChangeRequest) => queryClient.setQueryData<ChangeRequest[]>(keys.changes(projectId), (previous) => upsert(previous, item))
}

export function useCreateChange(projectId: string) {
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: (body: { source: ChangeSource; text?: string; edits?: ChangeEdits }) =>
      apiFetch<ChangeRequest>(project(projectId, '/changes'), { method: 'POST', json: body }),
    onSuccess: store,
  })
}

export function useAnswerChange(projectId: string) {
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: ({ changeId, answers }: { changeId: string; answers: Record<string, string> }) =>
      apiFetch<ChangeRequest>(change(changeId), { method: 'PATCH', json: { answers } }),
    onSuccess: store,
  })
}

/** Re-plan = C1's optimization run with the change's overrides, measured against the plan in force. */
export function useReplan(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { overrides: PlanOverrides; changeRequestId: string; baselineRunId: string | null }) =>
      apiFetch<OptimizationRun>(project(projectId, '/optimization/runs'), { method: 'POST', json: body }),
    onSuccess: (run) => {
      queryClient.setQueryData(keys.run(projectId, run.id), run)
      void queryClient.invalidateQueries({ queryKey: keys.changes(projectId) })
    },
  })
}

/** One optimization run; polls while it is queued or running. */
export function useRun(projectId: string, runId: string | null) {
  return useQuery({
    queryKey: keys.run(projectId, runId ?? ''),
    queryFn: ({ signal }) => apiFetch<OptimizationRun>(project(projectId, `/optimization/runs/${encodeURIComponent(runId ?? '')}`), { signal }),
    enabled: Boolean(projectId && runId),
    refetchInterval: (query) => (isRunning(query.state.data?.status) ? RUN_POLL_MS : false),
  })
}

export function useCompare(changeId: string | null, runId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.compare(changeId ?? '', runId ?? ''),
    queryFn: ({ signal }) =>
      apiFetch<ChangeComparison>(change(changeId ?? '', `/compare?runId=${encodeURIComponent(runId ?? '')}`), { signal }),
    enabled: Boolean(changeId && runId) && enabled,
  })
}

export function useDecideChange(projectId: string) {
  const queryClient = useQueryClient()
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: ({ changeId, decision, runId }: { changeId: string; decision: 'approve' | 'reject'; runId?: string | null }) =>
      apiFetch<ChangeRequest>(change(changeId, `/${decision}`), {
        method: 'POST',
        json: decision === 'approve' && runId ? { runId } : {},
      }),
    onSuccess: (item, { decision }) => {
      store(item)
      if (decision !== 'approve') return
      for (const key of [
        keys.versions(projectId),
        queryKeys.project(projectId),
        queryKeys.latestRun(projectId),
        queryKeys.constraints(projectId),
        queryKeys.wbs(projectId),
        queryKeys.capacity(projectId),
        keys.team(projectId),
        ['projects', projectId, 'team'],
        ['projects', projectId, 'overview'],
        queryKeys.notifications,
        ['orgs'],
      ]) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
  })
}

/** Text of an uploaded notes file: plain text is read here, other formats through A2's extractor. */
export async function readNotesFile(file: File, orgId: string | null): Promise<string> {
  if (file.type.startsWith('text/') || /\.(txt|md|vtt|srt|csv)$/i.test(file.name)) return file.text()
  if (!orgId) throw new Error('Paste the text of this file instead.')
  const body = new FormData()
  body.append('file', file, file.name)
  const draft = await apiFetch<{ text: string | null }>(`/api/v1/orgs/${encodeURIComponent(orgId)}/project-drafts/extract`, {
    method: 'POST',
    body,
  })
  if (!draft.text?.trim()) throw new Error("We couldn't read any text in this file. Paste the notes instead.")
  return draft.text
}
