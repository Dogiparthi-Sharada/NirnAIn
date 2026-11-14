/**
 * API paths shared by the query hooks.
 *
 * Purpose: Builds the project, run, intake-run and project-list URLs in one place, always
 * URL-encoding the ids.
 * How it fits: Used by the modules in `lib/queries/` and by `lib/changes.ts`, `lib/create.ts`,
 * `lib/inputs.ts` and `lib/optimize.ts`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import type { ProjectFilters } from './keys'

/** `/api/v1/projects/{projectId}{suffix}`, with the id URL-encoded. */
export function projectPath(projectId: string, suffix = ''): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`
}

/** Path of a project's optimization runs (`suffix` picks one run or an action on it). */
export function runPath(projectId: string, suffix = ''): string {
  return projectPath(projectId, `/optimization/runs${suffix}`)
}

/** Path of a project's AI intake runs (`suffix` picks the latest run). */
export function intakeRunPath(projectId: string, suffix = ''): string {
  return projectPath(projectId, `/intake/runs${suffix}`)
}

/** One page of an org's project list; `cursor` is the previous page's `nextCursor`. */
export function projectListPath(orgId: string, filters: ProjectFilters, cursor: string | null): string {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.search) params.set('q', filters.search)
  params.set('limit', String(filters.limit ?? 25))
  if (cursor) params.set('cursor', cursor)
  return `/api/v1/orgs/${encodeURIComponent(orgId)}/projects?${params.toString()}`
}
