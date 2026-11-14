/**
 * Per-project dashboard data: `GET /api/v1/projects/{projectId}/overview` (contract [A1],
 * backend `app/domain/overview.py`). The server never invents values: what isn't tracked comes back
 * null and the dashboard says so ("Not tracked yet", "Available after planning").
 * Used by `screens/Dashboard.tsx` and `components/DashboardCards.tsx`.
 */
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'
import type { AppNotification, ProjectStatus } from './types'

export type OverviewStepKey = 'intake' | 'scope' | 'wbs' | 'constraints' | 'optimization' | 'changes'

export interface OverviewKpi {
  label: string
  value: string
  sub?: string
}

export interface ProjectOverview {
  project: {
    id: string
    code: string
    name: string
    status: ProjectStatus
    /** Target end date, ISO. */
    dueDate: string | null
    pmName: string | null
    budgetCents: number | null
    currency: string
    progressPct: number
  }
  appliedVersion: number | null
  /** The applied plan (Plan vN in force), null before one is applied. */
  plan: {
    version: number
    runId: string
    appliedAt: string | null
    expectedCostCents: number | null
    completionDate: string | null
    kpis: OverviewKpi[]
    peakUtilization: number | null
    violations: { hard: number; soft: number }
  } | null
  /** Must hold / Can flex per constraint. */
  constraintChoices: { key: string; name: string; mode: 'hard' | 'soft'; label: string }[]
  milestones: {
    total: number
    next: { name: string; dueDate: string; status: 'On track' | 'At risk' | 'Not planned yet' } | null
  }
  risks: { level: 'High' | 'Medium'; text: string; source: string }[]
  notifications: AppNotification[]
  notificationTotal: number
  findings: { text: string; source: 'wbs_validate' | 'optimization' }[]
  /** Spend is not tracked yet (always null for now). */
  spentToDateCents: number | null
  /** The first unfinished workflow step. */
  nextStep: { label: string; step: OverviewStepKey }
}

/** One project's dashboard overview. */
export function useProjectOverview(projectId: string | null) {
  return useQuery({
    queryKey: ['projects', projectId ?? '', 'overview'] as const,
    queryFn: ({ signal }) =>
      apiFetch<ProjectOverview>(`/api/v1/projects/${encodeURIComponent(projectId ?? '')}/overview`, { signal }),
    enabled: Boolean(projectId),
  })
}
