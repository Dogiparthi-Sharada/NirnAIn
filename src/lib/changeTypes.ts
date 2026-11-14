/**
 * Wire types of step 7, Changes & Versions, mirroring the backend change and plan-version models.
 *
 * Purpose: Keeps the contract of `app/domain/changes.py` and the plan versions of
 * `app/domain/plan.py` in one place: detected changes, questions, re-plan overrides, comparisons
 * and the priced keep-the-date options.
 * How it fits: Re-exported by `lib/changes.ts`, which screens import from; types only, no runtime
 * code; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import type { RunStatus } from './types'

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
export type ChangeKind = 'budget' | 'deadline' | 'duration' | 'capacity' | 'scope' | 'team'
/** team: someone joins / leaves the project; scope: a requirement is added / removed. */
export type ChangeAction = 'add' | 'remove'
export type ChangeStatus = 'draft' | 'analyzing' | 'ready' | 'replanning' | 'proposed' | 'approved' | 'rejected' | 'failed'

export interface TargetOption {
  id: string
  label: string
  from: number | null
  to: number | null
}

/** Employees are already in the company (costed from their company record); contractors bring their own rate. */
export type WorkerType = 'employee' | 'contractor'

/**
 * Someone joining the project mid-way. An employee has no rate here; a contractor needs one
 * (the project's contractor rate is the default when set; otherwise the API answers 422).
 */
export interface NewMember {
  fullName: string
  roleTitle: string
  /** Whole hours per week on this project, 1–80. */
  hoursPerWeek: number
  skills: string[]
  workerType: WorkerType
  hourlyRateCents?: number | null
}

/** A newcomer in the re-plan overrides, with the organization employee the change reserved for them. */
export interface AddedMember extends NewMember {
  employeeId: string
}

/**
 * budget = cents, deadline = ISO date, duration = working days, capacity = hours/week (0 = leaves),
 * scope = text, team = hours/week of who joins (`member`; null until the notes say) or leaves (0).
 */
export interface DetectedChange {
  id: string
  kind: ChangeKind
  action: ChangeAction | null
  member: NewMember | null
  target: string | null
  targetLabel: string | null
  from: number | string | null
  to: number | string | null
  quote: string | null
  speaker: string | null
  confidence: number
  note: string | null
  candidates: TargetOption[]
  /** The policy the notes state with a new budget or deadline ("must hold" = hard); absent on older changes. */
  mode?: 'hard' | 'soft' | null
}

export interface ChangeQuestion {
  id: string
  kind: 'budget' | 'deadline' | 'duration' | 'capacity' | 'target' | 'team' | 'rate'
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
  addMembers?: AddedMember[]
  removeMembers?: string[]
  /** The policy the notes state with the chosen budget / deadline (sent to the re-plan as is). */
  budgetMode?: 'hard' | 'soft'
  deadlineMode?: 'hard' | 'soft'
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
  /** People joining the project (at most 10). */
  addMembers?: NewMember[]
  /** Employee ids of team members leaving the project. */
  removeMembers?: string[]
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

/**
 * A priced way to keep the target date after a re-plan slips (C1/H11 `result.remedies[]`), or to
 * get a plan at all; `combined` (round 3, R6) changes several inputs together when no single one
 * is enough (absent on older servers).
 */
export type KeepDateRemedyId = 'specialist' | 'overtime' | 'move_deadline' | 'combined'

export interface KeepDateRemedy {
  id: KeepDateRemedyId
  title: string
  detail: string
  /** Extra cost versus the re-planned plan, in cents. */
  costCents: number | null
  completionDate: string | null
  /** What the combined option changes, e.g. `{kind: 'deadline', label: 'Move go-live to Jun 24, 2027'}`. */
  changes?: { kind: 'deadline' | 'budget' | 'specialist' | 'capacity'; label: string }[]
}

/** What the re-plan says about the date: the date it tried to keep and how far it slipped. */
export interface KeepDate {
  completionDate: string | null
  /** For a step 7 re-plan: the completion date of the plan in force. */
  targetDate: string | null
  /** Working days after `targetDate` (0 = on time). */
  slipDays: number
  remedies: KeepDateRemedy[]
}

/** Who is on the project and the hours each gives it (B's team, else the org pool). */
export interface ChangePerson {
  employeeId: string
  fullName: string
  hoursPerWeek: number
}
