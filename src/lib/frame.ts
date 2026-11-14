/**
 * State of the signed-in app frame (`components/Shell.tsx`) that several screens share: which
 * project is open, whether it has an applied plan (unlocks step 7), what the assistant's context
 * is, and the last project the PM looked at (the dashboard's default and the sidebar's project
 * while on the dashboard).
 *
 * The last project lives in localStorage with a tiny subscription so the sidebar follows the
 * dashboard's picker immediately.
 */
import { useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useMatch } from 'react-router'
import { apiFetch } from './api'

/** One applied plan (contract [BE1] `GET /projects/{projectId}/plan-versions`). */
export interface PlanVersion {
  version: number
  runId: string
  appliedAt: string | null
  completionDate: string | null
  expectedCostCents: number | null
  solver: string | null
  label: string | null
}

export const frameKeys = {
  planVersions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
}

/** Applied plan versions, oldest first. Step 7 unlocks once there is at least one. */
export function usePlanVersions(projectId: string | undefined) {
  return useQuery({
    queryKey: frameKeys.planVersions(projectId ?? ''),
    queryFn: ({ signal }) =>
      apiFetch<PlanVersion[]>(`/api/v1/projects/${encodeURIComponent(projectId ?? '')}/plan-versions`, { signal }),
    enabled: Boolean(projectId),
    retry: false,
  })
}

/** `/projects/:projectId/...`, but not the static `/projects/new` routes. */
export function useCurrentProjectId(): string | undefined {
  const match = useMatch('/projects/:projectId/*')
  const projectId = match?.params.projectId
  return projectId && projectId !== 'new' ? projectId : undefined
}

/** Assistant context per screen (same names as the approved design). */
const CONTEXT_BY_SEGMENT: Record<string, string> = {
  intake: 'Intake',
  scope: 'Scope Review',
  wbs: 'WBS Builder',
  constraints: 'Constraints',
  optimization: 'Optimization Results',
  changes: 'Changes & Versions',
}

/** The screen name the assistant shows ("Context: …") and sends as `page`. */
export function screenContext(pathname: string): string {
  if (pathname.startsWith('/projects/new')) return 'Create Project'
  const segment = /^\/projects\/[^/]+\/([^/]+)/.exec(pathname)?.[1]
  return (segment && CONTEXT_BY_SEGMENT[segment]) || 'Dashboard'
}

export function useScreenContext(): string {
  return screenContext(useLocation().pathname)
}

/* ───────────────────────────── Last opened project ───────────────────────────── */

const LAST_PROJECT_KEY = 'nirnain:last-project'
const lastProjectListeners = new Set<() => void>()

export function readLastProjectId(): string | null {
  try {
    return localStorage.getItem(LAST_PROJECT_KEY)
  } catch {
    return null
  }
}

/** Remembers the project the PM opened last (in this browser) and tells subscribers. */
export function rememberLastProjectId(projectId: string): void {
  try {
    if (localStorage.getItem(LAST_PROJECT_KEY) === projectId) return
    localStorage.setItem(LAST_PROJECT_KEY, projectId)
  } catch {
    // Not remembering is harmless: the dashboard falls back to the newest project.
  }
  lastProjectListeners.forEach((listener) => listener())
}

function subscribeLastProject(listener: () => void): () => void {
  lastProjectListeners.add(listener)
  return () => {
    lastProjectListeners.delete(listener)
  }
}

/** The last opened project id, re-rendering when it changes. */
export function useLastProjectId(): string | null {
  return useSyncExternalStore(subscribeLastProject, readLastProjectId, () => null)
}

/* ───────────────────────────── Router state helpers ───────────────────────────── */

/** Reads `{ [key]: string }` from `location.state` (set via `navigate(path, { state })`). */
export function stateString(state: unknown, key: string): string | null {
  if (typeof state === 'object' && state !== null && key in state) {
    const value = (state as Record<string, unknown>)[key]
    return typeof value === 'string' ? value : null
  }
  return null
}
