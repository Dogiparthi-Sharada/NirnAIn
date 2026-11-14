/**
 * Query hooks for the signed-in user, organizations' members and projects.
 *
 * Purpose: Loads the profile and memberships, pages through an org's projects and creates
 * projects, keeping the org's lists fresh after a change.
 * How it fits: Used by `org/OrgProvider.tsx`, `screens/Dashboard.tsx`, `screens/CreateProject.tsx`
 * and `lib/create.ts`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { Me, Member, Page, Project, ProjectCreate } from '../types'
import { queryKeys, type ProjectFilters } from './keys'
import { projectListPath, projectPath } from './paths'

/** Profile data and memberships rarely change during a session. */
const PROFILE_STALE_MS = 5 * 60_000

/** Fetches the signed-in user and their organization memberships (`GET /me`). */
export function useMe(enabled = true) {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => apiFetch<Me>('/api/v1/me', { signal }),
    enabled,
    staleTime: PROFILE_STALE_MS,
  })
}

/** Fetches an org's projects page by page (cursor paging), keeping the last page while filters change. */
export function useProjects(orgId: string, filters: ProjectFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.projectList(orgId, filters),
    queryFn: ({ pageParam, signal }) => apiFetch<Page<Project>>(projectListPath(orgId, filters, pageParam), { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    placeholderData: keepPreviousData,
  })
}

/** Fetches one project; idle until there is a project id. */
export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ''),
    queryFn: ({ signal }) => apiFetch<Project>(projectPath(projectId ?? ''), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Fetches the members of an organization. */
export function useMembers(orgId: string) {
  return useQuery({
    queryKey: queryKeys.members(orgId),
    queryFn: ({ signal }) => apiFetch<Member[]>(`/api/v1/orgs/${encodeURIComponent(orgId)}/members`, { signal }),
    staleTime: PROFILE_STALE_MS,
  })
}

/** Returns a function that refreshes an org's project lists after a project is created. */
export function useInvalidateOrgProjects() {
  const queryClient = useQueryClient()
  return (orgId: string) => queryClient.invalidateQueries({ queryKey: queryKeys.projects(orgId) })
}

/** Creates a project in the org, caches it and refreshes the org's project lists. */
export function useCreateProject(orgId: string) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateOrgProjects()
  return useMutation({
    mutationFn: (body: ProjectCreate) =>
      apiFetch<Project>(`/api/v1/orgs/${encodeURIComponent(orgId)}/projects`, { method: 'POST', json: body }),
    onSuccess: (project) => {
      queryClient.setQueryData(queryKeys.project(project.id), project)
      return invalidate(orgId)
    },
  })
}
