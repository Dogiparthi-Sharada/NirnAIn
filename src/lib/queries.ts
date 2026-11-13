import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { apiFetch, isApiError } from './api'
import type {
  AiJob,
  AiJobKind,
  AppNotification,
  AssistantMessage,
  ConstraintSet,
  DashboardSummary,
  Dependency,
  DependencyCreate,
  DependencyType,
  IntakeRun,
  Me,
  Member,
  OptimizationRun,
  Page,
  Project,
  ProjectCreate,
  ProjectStatus,
  ProjectUpdate,
  ScopeCategory,
  ScopeItem,
  ScopeItemPatch,
  SkillCoverage,
  SourceDocument,
  TeamMember,
  WbsNode,
  WbsNodeCreate,
  WbsNodePatch,
} from './types'

export interface ProjectFilters {
  q?: string
  status?: ProjectStatus
  limit?: number
}

export const queryKeys = {
  me: ['me'] as const,
  projects: (orgId: string) => ['orgs', orgId, 'projects'] as const,
  projectList: (orgId: string, filters: ProjectFilters) => ['orgs', orgId, 'projects', 'list', filters] as const,
  atRisk: (orgId: string) => ['orgs', orgId, 'projects', 'at-risk'] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  dashboard: (orgId: string) => ['orgs', orgId, 'dashboard'] as const,
  members: (orgId: string) => ['orgs', orgId, 'members'] as const,
  notifications: ['notifications', { unread: true }] as const,
  latestRun: (projectId: string) => ['projects', projectId, 'optimization', 'latest'] as const,
  latestIntakeRun: (projectId: string) => ['projects', projectId, 'intake', 'latest'] as const,
  scopeItems: (projectId: string) => ['projects', projectId, 'scope-items'] as const,
  wbs: (projectId: string) => ['projects', projectId, 'wbs'] as const,
  latestAiJob: (projectId: string, kind: AiJobKind) => ['projects', projectId, 'ai-jobs', kind] as const,
  constraints: (projectId: string) => ['projects', projectId, 'constraints'] as const,
  capacity: (projectId: string) => ['projects', projectId, 'capacity'] as const,
  skillsCoverage: (projectId: string) => ['projects', projectId, 'skills-coverage'] as const,
  dependencies: (projectId: string) => ['projects', projectId, 'dependencies'] as const,
  documents: (projectId: string) => ['projects', projectId, 'documents'] as const,
}

function projectListPath(orgId: string, filters: ProjectFilters, cursor: string | null): string {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.q) params.set('q', filters.q)
  params.set('limit', String(filters.limit ?? 25))
  if (cursor) params.set('cursor', cursor)
  return `/api/v1/orgs/${encodeURIComponent(orgId)}/projects?${params.toString()}`
}

export function useMe(enabled = true) {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => apiFetch<Me>('/api/v1/me', { signal }),
    enabled,
    staleTime: 5 * 60_000,
  })
}

export function useProjects(orgId: string, filters: ProjectFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.projectList(orgId, filters),
    queryFn: ({ pageParam, signal }) => apiFetch<Page<Project>>(projectListPath(orgId, filters, pageParam), { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
    placeholderData: keepPreviousData,
  })
}

/** Projects that need attention: at-risk and delayed, soonest deadline first. */
export function useAtRiskProjects(orgId: string, limit = 5) {
  return useQuery({
    queryKey: queryKeys.atRisk(orgId),
    queryFn: async ({ signal }) => {
      const [atRisk, delayed] = await Promise.all(
        (['at_risk', 'delayed'] as const).map((status) =>
          apiFetch<Page<Project>>(projectListPath(orgId, { status, limit }, null), { signal }),
        ),
      )
      return [...atRisk.items, ...delayed.items]
        // Soonest due first; projects without a target date go last.
        .sort((a, b) => (a.targetEndDate ?? '9999-12-31').localeCompare(b.targetEndDate ?? '9999-12-31'))
        .slice(0, limit)
    },
  })
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ''),
    queryFn: ({ signal }) => apiFetch<Project>(`/api/v1/projects/${encodeURIComponent(projectId ?? '')}`, { signal }),
    enabled: Boolean(projectId),
  })
}

export function useDashboard(orgId: string) {
  return useQuery({
    queryKey: queryKeys.dashboard(orgId),
    queryFn: ({ signal }) =>
      apiFetch<DashboardSummary>(`/api/v1/orgs/${encodeURIComponent(orgId)}/dashboard`, { signal }),
  })
}

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: queryKeys.members(orgId),
    queryFn: ({ signal }) => apiFetch<Member[]>(`/api/v1/orgs/${encodeURIComponent(orgId)}/members`, { signal }),
    staleTime: 5 * 60_000,
  })
}

export function useNotifications() {
  return useQuery({
    queryKey: queryKeys.notifications,
    // The API returns a cursor page ({ items, nextCursor }); the dashboard shows the first page.
    queryFn: async ({ signal }) => (await apiFetch<Page<AppNotification>>('/api/v1/notifications?unread=true', { signal })).items,
    refetchInterval: 60_000,
  })
}

function useInvalidateOrgProjects() {
  const qc = useQueryClient()
  return (orgId: string) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.projects(orgId) }),
      qc.invalidateQueries({ queryKey: queryKeys.dashboard(orgId) }),
    ])
}

export function useCreateProject(orgId: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateOrgProjects()
  return useMutation({
    mutationFn: (body: ProjectCreate) =>
      apiFetch<Project>(`/api/v1/orgs/${encodeURIComponent(orgId)}/projects`, { method: 'POST', json: body }),
    onSuccess: (project) => {
      qc.setQueryData(queryKeys.project(project.id), project)
      return invalidate(orgId)
    },
  })
}

export function useUpdateProject() {
  const qc = useQueryClient()
  const invalidate = useInvalidateOrgProjects()
  return useMutation({
    mutationFn: ({ projectId, patch }: { projectId: string; patch: ProjectUpdate }) =>
      apiFetch<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: 'PATCH', json: patch }),
    onSuccess: (project) => {
      qc.setQueryData(queryKeys.project(project.id), project)
      return invalidate(project.orgId)
    },
  })
}

export function useDeleteProject() {
  const qc = useQueryClient()
  const invalidate = useInvalidateOrgProjects()
  return useMutation({
    mutationFn: ({ projectId }: { projectId: string; orgId: string }) =>
      apiFetch<void>(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
    onSuccess: (_data, { projectId, orgId }) => {
      qc.removeQueries({ queryKey: queryKeys.project(projectId) })
      return invalidate(orgId)
    },
  })
}

const RUN_POLL_MS = 2000

function runPath(projectId: string, suffix = ''): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/optimization/runs${suffix}`
}

/** Latest optimization run for a project (`null` when none yet); polls while it is queued or running. */
export function useLatestRun(projectId: string) {
  return useQuery({
    queryKey: queryKeys.latestRun(projectId),
    queryFn: async ({ signal }) => {
      try {
        return await apiFetch<OptimizationRun>(runPath(projectId, '/latest'), { signal })
      } catch (error) {
        if (isApiError(error) && error.status === 404) return null
        throw error
      }
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'queued' || status === 'running' ? RUN_POLL_MS : false
    },
  })
}

export function useStartRun(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<OptimizationRun>(runPath(projectId), { method: 'POST', json: {} }),
    onSuccess: (run) => queryClient.setQueryData(queryKeys.latestRun(projectId), run),
    onError: (error) => {
      // Someone else already started one: show it.
      if (isApiError(error) && error.status === 409) void queryClient.invalidateQueries({ queryKey: queryKeys.latestRun(projectId) })
    },
  })
}

const INTAKE_POLL_MS = 2000

function intakeRunPath(projectId: string, suffix = ''): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/intake/runs${suffix}`
}

/** Latest AI intake run for a project (`null` when none yet); polls while it is queued or running. */
export function useLatestIntakeRun(projectId: string) {
  return useQuery({
    queryKey: queryKeys.latestIntakeRun(projectId),
    queryFn: async ({ signal }) => {
      try {
        return await apiFetch<IntakeRun>(intakeRunPath(projectId, '/latest'), { signal })
      } catch (error) {
        if (isApiError(error) && error.status === 404) return null
        throw error
      }
    },
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'queued' || status === 'running' ? INTAKE_POLL_MS : false
    },
  })
}

export function useStartIntakeRun(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (text: string) => apiFetch<IntakeRun>(intakeRunPath(projectId), { method: 'POST', json: { text } }),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.latestIntakeRun(projectId), run)
      return queryClient.invalidateQueries({ queryKey: queryKeys.scopeItems(projectId) })
    },
    onError: (error) => {
      // Someone else already started one: show it.
      if (isApiError(error) && error.status === 409) void queryClient.invalidateQueries({ queryKey: queryKeys.latestIntakeRun(projectId) })
    },
  })
}

export function useScopeItems(projectId: string) {
  return useQuery({
    queryKey: queryKeys.scopeItems(projectId),
    queryFn: ({ signal }) =>
      apiFetch<ScopeItem[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/scope-items`, { signal }),
    enabled: Boolean(projectId),
  })
}

export function useAddScopeItem(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (item: { category: ScopeCategory; text: string }) =>
      apiFetch<ScopeItem>(`/api/v1/projects/${encodeURIComponent(projectId)}/scope-items`, { method: 'POST', json: item }),
    onSuccess: (item) => {
      queryClient.setQueryData<ScopeItem[]>(queryKeys.scopeItems(projectId), (prev) => (prev ? [...prev, item] : [item]))
    },
  })
}

export function useUpdateScopeItem(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ itemId, patch }: { itemId: string; patch: ScopeItemPatch }) =>
      apiFetch<ScopeItem>(`/api/v1/scope-items/${encodeURIComponent(itemId)}`, { method: 'PATCH', json: patch }),
    onSuccess: (item) => {
      queryClient.setQueryData<ScopeItem[]>(queryKeys.scopeItems(projectId), (prev) =>
        prev ? prev.map((i) => (i.id === item.id ? item : i)) : prev,
      )
    },
  })
}

/* ───────────────────────────── WBS ───────────────────────────── */

const AI_JOB_POLL_MS = 2000

function projectPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`
}

/** WBS changes renumber codes, which dependency labels and skill coverage are built from. */
function invalidatePlanning(queryClient: ReturnType<typeof useQueryClient>, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.dependencies(projectId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.skillsCoverage(projectId) })
}

export function useWbs(projectId: string) {
  return useQuery({
    queryKey: queryKeys.wbs(projectId),
    queryFn: ({ signal }) => apiFetch<WbsNode[]>(projectPath(projectId, '/wbs'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useCreateWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (node: WbsNodeCreate) => apiFetch<WbsNode>(projectPath(projectId, '/wbs/nodes'), { method: 'POST', json: node }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      invalidatePlanning(queryClient, projectId)
    },
  })
}

export function useUpdateWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ nodeId, patch }: { nodeId: string; patch: WbsNodePatch }) =>
      apiFetch<WbsNode>(`/api/v1/wbs/nodes/${encodeURIComponent(nodeId)}`, { method: 'PATCH', json: patch }),
    onSuccess: (node) => {
      queryClient.setQueryData<WbsNode[]>(queryKeys.wbs(projectId), (prev) => prev?.map((n) => (n.id === node.id ? node : n)))
      invalidatePlanning(queryClient, projectId)
    },
  })
}

export function useMoveWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ nodeId, position }: { nodeId: string; position: number }) =>
      apiFetch<WbsNode[]>(`/api/v1/wbs/nodes/${encodeURIComponent(nodeId)}/move`, { method: 'POST', json: { position } }),
    onSuccess: (tree) => {
      queryClient.setQueryData(queryKeys.wbs(projectId), tree)
      invalidatePlanning(queryClient, projectId)
    },
  })
}

/** Latest AI job of a kind (`null` when none yet); polls while it is queued or running. */
export function useLatestAiJob(projectId: string, kind: AiJobKind) {
  return useQuery({
    queryKey: queryKeys.latestAiJob(projectId, kind),
    queryFn: async ({ signal }) => {
      try {
        return await apiFetch<AiJob>(projectPath(projectId, `/ai-jobs/latest?kind=${kind}`), { signal })
      } catch (error) {
        if (isApiError(error) && error.status === 404) return null
        throw error
      }
    },
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'queued' || status === 'running' ? AI_JOB_POLL_MS : false
    },
  })
}

export function useStartAiJob(projectId: string, kind: AiJobKind) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiFetch<AiJob>(projectPath(projectId, kind === 'wbs_generate' ? '/wbs/generate' : '/wbs/validate'), { method: 'POST', json: {} }),
    onSuccess: (job) => queryClient.setQueryData(queryKeys.latestAiJob(projectId, kind), job),
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.latestAiJob(projectId, kind) })
        void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      }
    },
  })
}

/* ───────────────────────────── Constraints / resources ───────────────────────────── */

export function useConstraints(projectId: string) {
  return useQuery({
    queryKey: queryKeys.constraints(projectId),
    queryFn: ({ signal }) => apiFetch<ConstraintSet>(projectPath(projectId, '/constraints'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useSaveConstraints(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (constraints: ConstraintSet) =>
      apiFetch<ConstraintSet>(projectPath(projectId, '/constraints'), { method: 'PUT', json: constraints }),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.constraints(projectId), saved)
      void queryClient.invalidateQueries({ queryKey: queryKeys.capacity(projectId) })
    },
  })
}

export function useCapacity(projectId: string) {
  return useQuery({
    queryKey: queryKeys.capacity(projectId),
    queryFn: ({ signal }) => apiFetch<TeamMember[]>(projectPath(projectId, '/capacity'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useSkillsCoverage(projectId: string) {
  return useQuery({
    queryKey: queryKeys.skillsCoverage(projectId),
    queryFn: ({ signal }) => apiFetch<SkillCoverage[]>(projectPath(projectId, '/skills-coverage'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useDependencies(projectId: string) {
  return useQuery({
    queryKey: queryKeys.dependencies(projectId),
    queryFn: ({ signal }) => apiFetch<Dependency[]>(projectPath(projectId, '/dependencies'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useCreateDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (dep: DependencyCreate) => apiFetch<Dependency>(projectPath(projectId, '/dependencies'), { method: 'POST', json: dep }),
    onSuccess: (dep) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (prev) => (prev ? [...prev, dep] : [dep]))
    },
  })
}

export function useUpdateDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ dependencyId, type }: { dependencyId: string; type: DependencyType }) =>
      apiFetch<Dependency>(`/api/v1/dependencies/${encodeURIComponent(dependencyId)}`, { method: 'PATCH', json: { type } }),
    onSuccess: (dep) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (prev) => prev?.map((d) => (d.id === dep.id ? dep : d)))
    },
  })
}

export function useDeleteDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (dependencyId: string) =>
      apiFetch<void>(`/api/v1/dependencies/${encodeURIComponent(dependencyId)}`, { method: 'DELETE' }),
    onSuccess: (_void, dependencyId) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (prev) => prev?.filter((d) => d.id !== dependencyId))
    },
  })
}

/* ───────────────────────────── Optimization extras ───────────────────────────── */

export function useApplyRun(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      apiFetch<OptimizationRun>(runPath(projectId, `/${encodeURIComponent(runId)}/apply`), { method: 'POST', json: {} }),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.latestRun(projectId), run)
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      void queryClient.invalidateQueries({ queryKey: ['orgs'] })
    },
  })
}

/* ───────────────────────────── AI assistant ───────────────────────────── */

export function useAssistant() {
  return useMutation({
    mutationFn: (body: { projectId?: string; page: string; messages: AssistantMessage[] }) =>
      apiFetch<{ reply: string }>('/api/v1/assistant/messages', { method: 'POST', json: body }),
  })
}

/* ───────────────────────────── Source documents ───────────────────────────── */

/** Uploads one file for AI intake; the server extracts its text. */
export function uploadDocument(projectId: string, file: File): Promise<SourceDocument> {
  const body = new FormData()
  body.append('file', file, file.name)
  return apiFetch<SourceDocument>(projectPath(projectId, '/documents'), { method: 'POST', body })
}

export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: queryKeys.documents(projectId),
    queryFn: ({ signal }) => apiFetch<SourceDocument[]>(projectPath(projectId, '/documents'), { signal }),
    enabled: Boolean(projectId),
  })
}

export function useUploadDocument(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadDocument(projectId, file),
    onSuccess: (doc) => {
      queryClient.setQueryData<SourceDocument[]>(queryKeys.documents(projectId), (prev) => [doc, ...(prev ?? [])])
    },
  })
}

export function useDeleteDocument(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<void>(`/api/v1/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' }),
    onSuccess: (_void, documentId) => {
      queryClient.setQueryData<SourceDocument[]>(queryKeys.documents(projectId), (prev) => prev?.filter((d) => d.id !== documentId))
    },
  })
}
