/**
 * The "Plan vN is in force" info banner of the input steps (round 2, D3).
 *
 * Why: inputs stay editable after a plan is applied; the PM must know an edit only reaches
 * the plan through a new run on step 6 (applied as the next version), and that step 7 is
 * where to record why the plan changed. How it fits: shown at the top of AI Project Intake
 * (step 2) and Scope Review (step 3); it reads the applied plan versions through
 * `usePlanVersions` (`src/lib/changes.ts`) and renders nothing while no plan is applied.
 */
import { Link } from 'react-router'
import { usePlanVersions } from '@/lib/changes'
import { stepPath } from '@/lib/steps'

/** The banner for `projectId`, or nothing before Plan v1 (or while the versions load / fail). */
export function PlanInForceBanner({ projectId, id }: { projectId: string; id: string }) {
  const versions = usePlanVersions(projectId)
  if (!versions.data?.length) return null
  const version = Math.max(...versions.data.map((planVersion) => planVersion.version))
  return (
    <div className="banner row between" id={id} role="status">
      <span>
        <b>Plan v{version} is in force.</b> Changes here take effect when you re-run on step 6 and apply it as Plan v{version + 1}. To record why
        the plan changed (meeting notes, before/after comparison), use step 7.
      </span>
      <Link className="btn sm sec" to={stepPath('changes', projectId)}>
        Changes &amp; Versions &rarr;
      </Link>
    </div>
  )
}
