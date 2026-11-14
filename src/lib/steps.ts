/**
 * The numbered planning flow (steps 1–7) and navigation between its screens.
 *
 * Purpose: One list of steps keeps the sidebar, the stepper and every "Continue" button in the
 * same order and on the same URLs.
 * How it fits: Used by `components/Shell.tsx` and the step screens; routes live under
 * `/projects/:projectId/` in `App.tsx`; layer: hook/lib.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useCallback } from 'react'
import { useNavigate, useParams } from 'react-router'

/** The numbered planning flow. `segment` is the route under `/projects/:projectId/`. */
export const FLOW_STEPS = [
  { key: 'create', label: 'Create Project', segment: null },
  { key: 'intake', label: 'AI Project Intake', segment: 'intake' },
  { key: 'scope', label: 'Scope Review', segment: 'scope' },
  { key: 'wbs', label: 'WBS Builder', segment: 'wbs' },
  { key: 'constraints', label: 'Constraints', segment: 'constraints' },
  { key: 'optimization', label: 'Optimization Results', segment: 'optimization' },
  { key: 'changes', label: 'Changes & Versions', segment: 'changes' },
] as const

export type FlowStepKey = (typeof FLOW_STEPS)[number]['key']

/** The URL of a flow step for a project; `/projects/new` for step 1 or when there is no project yet. */
export function stepPath(step: FlowStepKey, projectId: string | undefined): string {
  const flowStep = FLOW_STEPS.find((candidate) => candidate.key === step)
  if (!flowStep?.segment || !projectId) return '/projects/new'
  return `/projects/${encodeURIComponent(projectId)}/${flowStep.segment}`
}

/** Navigate between flow steps of the project in the current URL. */
export function useStepNav(): (step: FlowStepKey) => void {
  const navigate = useNavigate()
  const { projectId } = useParams()
  return useCallback((step: FlowStepKey) => navigate(stepPath(step, projectId)), [navigate, projectId])
}
