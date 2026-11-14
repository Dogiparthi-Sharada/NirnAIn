/**
 * The "Plan vN is in force" banner of the input steps.
 *
 * Purpose: Inputs stay editable after a plan is applied, so the PM must know an edit only reaches
 * the plan through a new run on step 6 (applied as the next version) and that step 7 records why
 * the plan changed.
 * How it fits: Shown at the top of AI Project Intake and Scope Review; reads the applied plan
 * versions through `usePlanVersions` (`lib/changes.ts`) and renders nothing while no plan is
 * applied; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
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
