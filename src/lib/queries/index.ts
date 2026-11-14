/**
 * Public entry of the shared query hooks: re-exports every per-domain module under `lib/queries/`.
 *
 * Purpose: Screens keep importing from `@/lib/queries` while the hooks live in small modules, one
 * per domain (projects, intake, scope, WBS, constraints, dependencies, optimization, documents,
 * assistant).
 * How it fits: Imported by screens, components and the other `lib/` data modules; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

export { queryKeys, type ProjectFilters } from './keys'
export { isRunning, pollWhileRunning, POLL_INTERVAL_MS } from './polling'
export { useCreateProject, useInvalidateOrgProjects, useMe, useMembers, useProject, useProjects } from './projects'
export { useLatestIntakeRun, useStartIntakeRun } from './intake'
export { useAddScopeItem, useScopeItems, useUpdateScopeItem } from './scope'
export { useCreateWbsNode, useLatestAiJob, useMoveWbsNode, useStartAiJob, useUpdateWbsNode, useWbs } from './wbs'
export { useCapacity, useConstraints, useSaveConstraints } from './constraints'
export { useCreateDependency, useDeleteDependency, useDependencies, useUpdateDependency } from './dependencies'
export { useApplyRun, useLatestRun } from './optimization'
export { uploadDocument, useDeleteDocument, useDocuments, useUploadDocument } from './documents'
export { useAssistant } from './assistant'
