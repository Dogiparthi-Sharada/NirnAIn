/**
 * Planning inputs for steps 3–5: the team, the project's skill list, the constraint answers and plan versions.
 *
 * Purpose: Scope Review, WBS Builder and Constraints share the same solver inputs and the rules on
 * them (utilization, worker types and cost rates, skill gaps); any change to them must refresh the
 * step 6 confirmation sheet and the coverage views.
 * How it fits: Types and hooks used by `screens/Constraints.tsx`, `screens/constraints/*`,
 * `screens/ScopeReview.tsx` and `lib/constraints.ts`; layer: hook/lib.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import { queryKeys } from './queries'
import { projectPath } from './queries/paths'
import type { ConstraintSet, ScopeItem } from './types'

/* ───────────────────────────── Types ───────────────────────────── */

export type Evidence = 'stated' | 'inferred' | 'flagged'

/** A scope item with the provenance the intake records (fields may be absent on older servers). */
export interface EvidenceScopeItem extends ScopeItem {
  sourceQuote?: string | null
  evidence?: Evidence | null
  evidenceNote?: string | null
  suggestedText?: string | null
}

export type SpecialistAnswer = 'yes' | 'no' | 'unknown'
export type DeadlineAnswerValue = 'hard' | 'soft' | 'unknown'

export interface ConstraintInputs extends ConstraintSet {
  specialistAnswer?: SpecialistAnswer | null
  deadlineAnswer?: DeadlineAnswerValue | null
  specialistRateConfirmed?: boolean
  contractorRateCents?: number | null
  allowContractors?: boolean
}

/**
 * Who a person is to the company, which decides where their cost comes from: an employee is
 * costed at the rate on their company record (else the standard internal rate), a contractor at
 * the hourly rate agreed for them. Both count toward the budget.
 */
export type WorkerType = 'employee' | 'contractor'

export interface ProjectTeamMember {
  employeeId: string
  fullName: string
  roleTitle: string | null
  hoursPerWeek: number
  /** Absent on older servers, which know only employees. */
  workerType?: WorkerType
  /**
   * Employees: the rate on their company record (null = none, so the standard rate applies).
   * Contractors: their agreed rate (null = the project's contractor rate).
   */
  hourlyRateCents?: number | null
  /** Average weekly hours already committed to other projects in this plan's window. */
  bookedHours: number
  skills: string[]
}

export interface TeamMemberInput {
  fullName: string
  roleTitle: string
  hoursPerWeek: number
  /** Omitted = employee (the server's default). */
  workerType?: WorkerType
  /** Contractors only: an employee's rate comes from the company record and the server refuses one here. */
  hourlyRateCents?: number | null
  skills: string[]
}

/** A skill on the project's list (step 5), Required or Optional. */
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

/**
 * The plan version in force (the latest applied one), or null before any plan is applied. Inputs
 * stay editable either way; it only tells the screens which version a re-run would replace.
 */
export function versionInForce(versions: PlanVersion[] | undefined): number | null {
  if (!versions || versions.length === 0) return null
  return Math.max(...versions.map((planVersion) => planVersion.version))
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

/** Booked hours as a % of the hours a member can give this project. */
export function utilization(member: ProjectTeamMember): number {
  return member.hoursPerWeek ? Math.round((member.bookedHours / member.hoursPerWeek) * 100) : 0
}

/** True for whole hours per week from 1 to 80 (what the API accepts). */
export function isWholeHours(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 80
}

/** The standard internal rate for employees without a company rate ($85/h; backend `DEFAULT_HOURLY_RATE_CENTS`). */
export const DEFAULT_HOURLY_RATE_CENTS = 8_500
/** The API accepts rates up to $1,000,000/h. */
const MAX_HOURLY_RATE_CENTS = 100_000_000

/** A person's worker type; older servers don't send one and know only employees. */
export function workerTypeOf(member: ProjectTeamMember): WorkerType {
  return member.workerType ?? 'employee'
}

/** A contractor's rate: more than $0 and at most what the API accepts. */
export function isHourlyRate(cents: number): boolean {
  return Number.isInteger(cents) && cents > 0 && cents <= MAX_HOURLY_RATE_CENTS
}

/** "$95/h", "$175.50/h". */
export function perHourText(cents: number): string {
  return `$${(cents / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}/h`
}

/**
 * Where a person's cost comes from, as the team table shows it: "Employee · company rate",
 * "Employee · standard $85/h" (no rate on their company record), "Contractor · $185/h", or
 * "Contractor · project rate $160/h" when they use the project's contractor rate.
 */
export function costRateText(member: ProjectTeamMember, projectContractorRateCents: number | null): string {
  if (workerTypeOf(member) === 'employee') {
    return member.hourlyRateCents != null ? 'Employee \u00B7 company rate' : `Employee \u00B7 standard ${perHourText(DEFAULT_HOURLY_RATE_CENTS)}`
  }
  if (member.hourlyRateCents != null) return `Contractor \u00B7 ${perHourText(member.hourlyRateCents)}`
  return projectContractorRateCents != null ? `Contractor \u00B7 project rate ${perHourText(projectContractorRateCents)}` : 'Contractor \u00B7 rate needed'
}

/** Required skills nobody on the team has. */
export function skillGaps(skills: ProjectSkill[]): string[] {
  return skills.filter((skill) => skill.required && skill.holders.length === 0).map((skill) => skill.name)
}

/* ───────────────────────────── Hooks ───────────────────────────── */

export const inputKeys = {
  team: (projectId: string) => ['projects', projectId, 'team'] as const,
  skills: (projectId: string) => ['projects', projectId, 'skills'] as const,
  planVersions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
}

/** Solver inputs changed: the confirmation sheet on step 6 and coverage views must refresh. */
function inputsChanged(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: inputKeys.team(projectId) })
  void queryClient.invalidateQueries({ queryKey: inputKeys.skills(projectId) })
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

/** Fetches the constraint set including the step 5 answers. */
export function useConstraintInputs(projectId: string) {
  return useQuery({
    queryKey: queryKeys.constraints(projectId),
    queryFn: ({ signal }) => apiFetch<ConstraintInputs>(projectPath(projectId, '/constraints'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Fetches the project's team with each person's hours, booked hours and skills. */
export function useTeam(projectId: string) {
  return useQuery({
    queryKey: inputKeys.team(projectId),
    queryFn: ({ signal }) => apiFetch<ProjectTeamMember[]>(projectPath(projectId, '/team'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Adds a person to the team and refreshes every solver input that depends on the team. */
export function useAddTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (member: TeamMemberInput) =>
      apiFetch<ProjectTeamMember>(projectPath(projectId, '/team'), { method: 'POST', json: member }),
    onSuccess: () => inputsChanged(queryClient, projectId),
  })
}

/** Saves a change to a team member, swaps it into the cached team and refreshes the solver inputs. */
export function useUpdateTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ employeeId, patch }: { employeeId: string; patch: Partial<TeamMemberInput> }) =>
      apiFetch<ProjectTeamMember>(projectPath(projectId, `/team/${encodeURIComponent(employeeId)}`), {
        method: 'PATCH',
        json: patch,
      }),
    onSuccess: (member) => {
      queryClient.setQueryData<ProjectTeamMember[]>(inputKeys.team(projectId), (previous) =>
        previous?.map((existing) => (existing.employeeId === member.employeeId ? member : existing)),
      )
      inputsChanged(queryClient, projectId)
    },
  })
}

/** Removes a person from the team, drops them from the cache and refreshes the solver inputs. */
export function useRemoveTeamMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (employeeId: string) =>
      apiFetch<void>(projectPath(projectId, `/team/${encodeURIComponent(employeeId)}`), { method: 'DELETE' }),
    onSuccess: (_, employeeId) => {
      queryClient.setQueryData<ProjectTeamMember[]>(inputKeys.team(projectId), (previous) =>
        previous?.filter((existing) => existing.employeeId !== employeeId),
      )
      inputsChanged(queryClient, projectId)
    },
  })
}

/** The project's skill list with who on the team has each skill. */
export function useProjectSkills(projectId: string) {
  return useQuery({
    queryKey: inputKeys.skills(projectId),
    queryFn: ({ signal }) => apiFetch<ProjectSkill[]>(projectPath(projectId, '/skills'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Adds a skill to the project's list (names are unique, case-insensitive). */
export function useAddSkill(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (skill: { name: string; required: boolean }) =>
      apiFetch<ProjectSkill>(projectPath(projectId, '/skills'), { method: 'POST', json: skill }),
    onSuccess: () => inputsChanged(queryClient, projectId),
  })
}

/** Renames a skill or switches it between Required and Optional. */
export function useUpdateSkill(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ skillId, patch }: { skillId: string; patch: { name?: string; required?: boolean } }) =>
      apiFetch<ProjectSkill>(projectPath(projectId, `/skills/${encodeURIComponent(skillId)}`), {
        method: 'PATCH',
        json: patch,
      }),
    onSuccess: () => inputsChanged(queryClient, projectId),
  })
}

/** Removes a skill from the project's list. */
export function useRemoveSkill(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (skillId: string) =>
      apiFetch<void>(projectPath(projectId, `/skills/${encodeURIComponent(skillId)}`), { method: 'DELETE' }),
    onSuccess: () => inputsChanged(queryClient, projectId),
  })
}
