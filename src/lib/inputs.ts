/**
 * Planning inputs for steps 3–5 (Scope Review, WBS Builder, Constraints): types and hooks for
 * the team, confirmed effort estimates, the answer to the deadline question, and the applied plan
 * versions. Skills are not part of planning: every team member can do every work package.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import { queryKeys } from './queries'
import type { ConstraintSet, ScopeItem, WbsNode } from './types'

/* ───────────────────────────── Types ───────────────────────────── */

export type Evidence = 'stated' | 'inferred' | 'flagged'

/** A scope item with the provenance the intake records (fields may be absent on older servers). */
export interface EvidenceScopeItem extends ScopeItem {
  sourceQuote?: string | null
  evidence?: Evidence | null
  evidenceNote?: string | null
  suggestedText?: string | null
}

export type EstimateSource = 'ai' | 'yours'

export interface EstimatedWbsNode extends WbsNode {
  estimateConfirmed?: boolean
  estimateSource?: EstimateSource | null
}

export type DeadlineAnswerValue = 'hard' | 'soft' | 'unknown'

export interface ConstraintInputs extends ConstraintSet {
  deadlineAnswer?: DeadlineAnswerValue | null
  specialistRateConfirmed?: boolean
  contractorRateCents?: number | null
  allowContractors?: boolean
}

export interface ProjectTeamMember {
  employeeId: string
  fullName: string
  roleTitle: string | null
  hoursPerWeek: number
  /** The person's hourly rate; null (or absent on older servers) = planned at the default rate. */
  hourlyRateCents?: number | null
  /** Average weekly hours already committed to other projects in this plan's window. */
  bookedHours: number
}

export interface TeamMemberInput {
  fullName: string
  roleTitle: string
  hoursPerWeek: number
  /** Null = no rate of their own: the optimizer plans them at `DEFAULT_HOURLY_RATE_CENTS`. */
  hourlyRateCents?: number | null
}

/** A skill on the project's old skill list; only read by step 7's add-person form until it drops skills (D8). */
export interface ProjectSkill {
  skillId: string
  name: string
  required: boolean
  /** Team members who have it. */
  holders: string[]
}

export interface PlanVersion {
  version: number
  runId: string
  appliedAt: string
  completionDate: string | null
  expectedCostCents: number | null
  solver: string | null
  label: string | null
}

/** Suggestions for the Role field (free text is still allowed). */
export const ROLES = [
  'Lead Architect',
  'Data Engineer',
  'BI Developer',
  'DevOps',
  'Security Eng.',
  'SAP Specialist',
  'QA Engineer',
  'Business Analyst',
  'Project Manager',
]

/* ───────────────────────────── Rules shared by the screens ───────────────────────────── */

/** 1 week = 5 working days on the wire. */
export const DAYS_PER_WEEK = 5

export function weeksLabel(days: number | null | undefined): string {
  if (days == null) return '—'
  const w = days / DAYS_PER_WEEK
  return `${Number.isInteger(w) ? w : w.toFixed(1)}w`
}

/** True when an AI estimate still waits for a person (older servers: AI durations count as open). */
export function estimateOpen(node: EstimatedWbsNode): boolean {
  if (node.level !== 3 || node.durationDays == null) return false
  const source = node.estimateSource ?? (node.origin === 'ai' ? 'ai' : 'yours')
  return source === 'ai' && !node.estimateConfirmed
}

/** Booked hours as a % of the hours a member can give this project. */
export function utilization(m: ProjectTeamMember): number {
  return m.hoursPerWeek ? Math.round((m.bookedHours / m.hoursPerWeek) * 100) : 0
}

export function isWholeHours(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 80
}

/** Rate the optimizer plans people without one at ($85/h, a blended IT rate; backend model.py). */
export const DEFAULT_HOURLY_RATE_CENTS = 8_500
/** The API accepts rates up to $1,000,000/h. */
const MAX_HOURLY_RATE_CENTS = 100_000_000

export function isHourlyRate(cents: number): boolean {
  return Number.isInteger(cents) && cents >= 0 && cents <= MAX_HOURLY_RATE_CENTS
}

/** "$95/h", or "default $85/h" for someone without a rate of their own. */
export function hourlyRateText(cents: number | null | undefined): string {
  const dollars = (value: number) => `$${(value / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}/h`
  return cents == null ? `default ${dollars(DEFAULT_HOURLY_RATE_CENTS)}` : dollars(cents)
}

/* ───────────────────────────── Hooks ───────────────────────────── */

export const inputKeys = {
  team: (projectId: string) => ['projects', projectId, 'team'] as const,
  skills: (projectId: string) => ['projects', projectId, 'skills'] as const,
  planVersions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
}

function projectPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`
}

type QueryClient = ReturnType<typeof useQueryClient>

/** Solver inputs changed: the confirmation sheet on step 6 and coverage views must refresh. */
function inputsChanged(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: inputKeys.team(projectId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.capacity(projectId) })
  void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'optimization'] })
}

/** Applied plan versions, oldest first. An older server without the endpoint means none. */
export function usePlanVersions(projectId: string) {
  return useQuery({
    queryKey: inputKeys.planVersions(projectId),
    queryFn: async ({ signal }) => {
      try {
        return await apiFetch<PlanVersion[]>(projectPath(projectId, '/plan-versions'), { signal })
      } catch (error) {
        if (isApiError(error) && (error.status === 404 || error.status === 405)) return []
        throw error
      }
    },
    enabled: Boolean(projectId),
  })
}

export function useConfirmEstimates(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (nodeIds?: string[]) =>
      apiFetch<EstimatedWbsNode[]>(projectPath(projectId, '/wbs/confirm-estimates'), {
        method: 'POST',
        json: nodeIds ? { nodeIds } : {},
      }),
    onSuccess: (tree) => {
      queryClient.setQueryData(queryKeys.wbs(projectId), tree)
      void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'optimization'] })
    },
  })
}

export function useConstraintInputs(projectId: string) {
  return useQuery({
    queryKey: queryKeys.constraints(projectId),
    queryFn: ({ signal }) => apiFetch<ConstraintInputs>(projectPath(projectId, '/constraints'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useSaveConstraintInputs(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (constraints: ConstraintInputs) =>
      apiFetch<ConstraintInputs>(projectPath(projectId, '/constraints'), { method: 'PUT', json: constraints }),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.constraints(projectId), saved)
      inputsChanged(queryClient, projectId)
    },
  })
}

export function useTeam(projectId: string) {
  return useQuery({
    queryKey: inputKeys.team(projectId),
    queryFn: ({ signal }) => apiFetch<ProjectTeamMember[]>(projectPath(projectId, '/team'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useAddTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (member: TeamMemberInput) =>
      apiFetch<ProjectTeamMember>(projectPath(projectId, '/team'), { method: 'POST', json: member }),
    onSuccess: () => inputsChanged(queryClient, projectId),
  })
}

export function useUpdateTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ employeeId, patch }: { employeeId: string; patch: Partial<TeamMemberInput> }) =>
      apiFetch<ProjectTeamMember>(projectPath(projectId, `/team/${encodeURIComponent(employeeId)}`), {
        method: 'PATCH',
        json: patch,
      }),
    onSuccess: (member) => {
      queryClient.setQueryData<ProjectTeamMember[]>(inputKeys.team(projectId), (prev) =>
        prev?.map((m) => (m.employeeId === member.employeeId ? member : m)),
      )
      inputsChanged(queryClient, projectId)
    },
  })
}

export function useRemoveTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (employeeId: string) =>
      apiFetch<void>(projectPath(projectId, `/team/${encodeURIComponent(employeeId)}`), { method: 'DELETE' }),
    onSuccess: (_, employeeId) => {
      queryClient.setQueryData<ProjectTeamMember[]>(inputKeys.team(projectId), (prev) =>
        prev?.filter((m) => m.employeeId !== employeeId),
      )
      inputsChanged(queryClient, projectId)
    },
  })
}

/** The project's old skill list (see `ProjectSkill`); remove with step 7's last use of it. */
export function useProjectSkills(projectId: string) {
  return useQuery({
    queryKey: inputKeys.skills(projectId),
    queryFn: ({ signal }) => apiFetch<ProjectSkill[]>(projectPath(projectId, '/skills'), { signal }),
    enabled: Boolean(projectId),
  })
}
