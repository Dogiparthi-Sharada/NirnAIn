/**
 * TanStack Query keys of the shared queries and the project-list filters.
 *
 * Purpose: Every hook and every invalidation must use the same keys; org keys start with
 * `['orgs', orgId]` and project keys with `['projects', projectId]` so one prefix refreshes
 * everything under it.
 * How it fits: Used by every module in `lib/queries/` and by the domain modules in `lib/` that
 * refresh shared data; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import type { AiJobKind, ProjectStatus } from '../types'

/** Filters of the dashboard's project list; part of its query key. */
export interface ProjectFilters {
  /** Free-text search, sent as `?q=`. */
  search?: string
  status?: ProjectStatus
  limit?: number
}

/**
 * TanStack Query keys of the shared queries. Org-scoped keys start with `['orgs', orgId]` and
 * project-scoped ones with `['projects', projectId]`, so a prefix invalidates everything below it.
 */
export const queryKeys = {
  me: ['me'] as const,
  projects: (orgId: string) => ['orgs', orgId, 'projects'] as const,
  projectList: (orgId: string, filters: ProjectFilters) => ['orgs', orgId, 'projects', 'list', filters] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  members: (orgId: string) => ['orgs', orgId, 'members'] as const,
  latestRun: (projectId: string) => ['projects', projectId, 'optimization', 'latest'] as const,
  latestIntakeRun: (projectId: string) => ['projects', projectId, 'intake', 'latest'] as const,
  scopeItems: (projectId: string) => ['projects', projectId, 'scope-items'] as const,
  wbs: (projectId: string) => ['projects', projectId, 'wbs'] as const,
  wbsChecks: (projectId: string) => ['projects', projectId, 'wbs-checks'] as const,
  latestAiJob: (projectId: string, kind: AiJobKind) => ['projects', projectId, 'ai-jobs', kind] as const,
  constraints: (projectId: string) => ['projects', projectId, 'constraints'] as const,
  capacity: (projectId: string) => ['projects', projectId, 'capacity'] as const,
  dependencies: (projectId: string) => ['projects', projectId, 'dependencies'] as const,
  documents: (projectId: string) => ['projects', projectId, 'documents'] as const,
}
