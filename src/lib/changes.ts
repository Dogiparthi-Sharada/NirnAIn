/**
 * Data layer of step 7, Changes & Versions: TanStack Query hooks and run helpers.
 *
 * Purpose: After a plan is applied, a change (meeting notes, direct edits or a progress update) is
 * analysed by the backend into detected changes and questions; once answered it is re-planned
 * against the plan in force and approved or rejected. Approving a plan invalidates every query it
 * changes.
 * How it fits: Wraps every endpoint of the flow (plan versions, change requests, team, re-plan
 * run, comparison, decision, notes extraction) for `screens/ChangesVersions.tsx` and
 * `screens/changes/*`; re-exports the wire types from `changeTypes.ts`; layer: hook/lib.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import type {
  ChangeComparison,
  ChangeEdits,
  ChangePerson,
  ChangeRequest,
  ChangeSource,
  KeepDate,
  KeepDateRemedy,
  KeepDateRemedyId,
  PlanOverrides,
  PlanVersion,
} from './changeTypes'
import { isRunning, pollWhileRunning, queryKeys } from './queries'
import { projectPath } from './queries/paths'
import type { OptimizationRun, RunResult, RunStatus, TeamMember } from './types'

export type * from './changeTypes'
export { isRunning }

interface DateSummary {
  completionDate?: string | null
  targetDate?: string | null
  slipDays?: number | null
}

/** The date facts of a run (`result.summary` and `result.remedies`; absent on older runs). */
export function keepDateOf(run: OptimizationRun): KeepDate {
  const result = run.result as (RunResult & { summary?: DateSummary | null; remedies?: KeepDateRemedy[] }) | null
  return {
    completionDate: result?.summary?.completionDate ?? null,
    targetDate: result?.summary?.targetDate ?? null,
    slipDays: result?.summary?.slipDays ?? 0,
    remedies: result?.remedies ?? [],
  }
}

const keys = {
  versions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
  changes: (projectId: string) => ['projects', projectId, 'changes'] as const,
  team: (projectId: string) => ['projects', projectId, 'changes', 'team'] as const,
  run: (projectId: string, runId: string) => ['projects', projectId, 'optimization', 'runs', runId] as const,
  compare: (changeId: string, runId: string) => ['changes', changeId, 'compare', runId] as const,
}

/** `/api/v1/changes/{changeId}{suffix}`, with the id URL-encoded. */
function changePath(changeId: string, suffix = ''): string {
  return `/api/v1/changes/${encodeURIComponent(changeId)}${suffix}`
}

/** True when a run ended with a usable plan (optimal or feasible). */
export function isSolved(status: RunStatus | null | undefined): boolean {
  return status === 'optimal' || status === 'feasible'
}

/** Fetches the applied plan versions (Plan v1, v2…), oldest first. */
export function usePlanVersions(projectId: string) {
  return useQuery({
    queryKey: keys.versions(projectId),
    queryFn: ({ signal }) => apiFetch<PlanVersion[]>(projectPath(projectId, '/plan-versions'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Fetches the project's change requests, newest first. */
export function useChanges(projectId: string, enabled = true) {
  return useQuery({
    queryKey: keys.changes(projectId),
    queryFn: ({ signal }) => apiFetch<ChangeRequest[]>(projectPath(projectId, '/changes'), { signal }),
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
          projectPath(projectId, '/team'),
          { signal },
        )
        if (team.length > 0) return team.map(({ employeeId, fullName, hoursPerWeek }) => ({ employeeId, fullName, hoursPerWeek }))
      } catch (error) {
        if (!isApiError(error) || error.status !== 404) throw error
      }
      const pool = await apiFetch<TeamMember[]>(projectPath(projectId, '/capacity'), { signal })
      return pool.map((member) => ({ employeeId: member.employeeId, fullName: member.fullName, hoursPerWeek: member.weeklyCapacityHours }))
    },
    enabled: Boolean(projectId) && enabled,
  })
}

/** Puts a change request into the cached list: replaced in place when known, else added first. */
function upsert(list: ChangeRequest[] | undefined, item: ChangeRequest): ChangeRequest[] {
  if (!list) return [item]
  return list.some((entry) => entry.id === item.id) ? list.map((entry) => (entry.id === item.id ? item : entry)) : [item, ...list]
}

/** Returns a function that writes a saved change request into the cached list. */
function useStoreChange(projectId: string) {
  const queryClient = useQueryClient()
  return (item: ChangeRequest) => queryClient.setQueryData<ChangeRequest[]>(keys.changes(projectId), (previous) => upsert(previous, item))
}

/** Creates a change request from notes, edits or a progress update and caches it. */
export function useCreateChange(projectId: string) {
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: (body: { source: ChangeSource; text?: string; edits?: ChangeEdits }) =>
      apiFetch<ChangeRequest>(projectPath(projectId, '/changes'), { method: 'POST', json: body }),
    onSuccess: store,
  })
}

/** Saves the answers to a change's questions and caches the updated change. */
export function useAnswerChange(projectId: string) {
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: ({ changeId, answers }: { changeId: string; answers: Record<string, string> }) =>
      apiFetch<ChangeRequest>(changePath(changeId), { method: 'PATCH', json: { answers } }),
    onSuccess: store,
  })
}

/** Re-plan = C1's optimization run with the change's overrides, measured against the plan in force. */
export function useReplan(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { overrides: PlanOverrides; changeRequestId: string; baselineRunId: string | null; remedy?: KeepDateRemedyId }) =>
      apiFetch<OptimizationRun>(projectPath(projectId, '/optimization/runs'), { method: 'POST', json: body }),
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
    queryFn: ({ signal }) => apiFetch<OptimizationRun>(projectPath(projectId, `/optimization/runs/${encodeURIComponent(runId ?? '')}`), { signal }),
    enabled: Boolean(projectId && runId),
    refetchInterval: (query) => pollWhileRunning(query.state.data?.status),
  })
}

/** Fetches the before/after comparison of a change's re-plan against the plan in force. */
export function useCompare(changeId: string | null, runId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.compare(changeId ?? '', runId ?? ''),
    queryFn: ({ signal }) =>
      apiFetch<ChangeComparison>(changePath(changeId ?? '', `/compare?runId=${encodeURIComponent(runId ?? '')}`), { signal }),
    enabled: Boolean(changeId && runId) && enabled,
  })
}

/**
 * Approves or rejects a change; an approval applies a new plan version, so every query it changes is
 * refreshed.
 */
export function useDecideChange(projectId: string) {
  const queryClient = useQueryClient()
  const store = useStoreChange(projectId)
  return useMutation({
    mutationFn: ({ changeId, decision, runId }: { changeId: string; decision: 'approve' | 'reject'; runId?: string | null }) =>
      apiFetch<ChangeRequest>(changePath(changeId, `/${decision}`), {
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
        ['projects', projectId, 'skills'],
        queryKeys.scopeItems(projectId),
        ['projects', projectId, 'overview'],
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
