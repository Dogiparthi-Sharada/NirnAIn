/**
 * Step 7, Changes & Versions (`/projects/:projectId/changes`), ported from the approved
 * mockup (`views/changes.js`, `changes-impact.js`, `changes-compare.js`).
 *
 * Something changed after a plan was applied: new meeting notes, direct edits or a
 * progress update. NirnAIn shows what it affects, re-plans with as little disruption as
 * possible (C1's optimizer, the plan in force as baseline) and keeps Plan vN in force until
 * Plan vN+1 is approved. Locked until a plan is applied (BE1's plan versions are empty).
 *
 * Data: `src/lib/changes.ts` (plan versions, change requests, re-plan run, comparison).
 * Panels live in `src/screens/changes/`.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { errorMessage } from '@/lib/api'
import { isSolved, useChanges, usePlanVersions, useRun, type ChangeRequest, type PlanVersion } from '@/lib/changes'
import { formatDate, formatMoney } from '@/lib/format'
import { useProject } from '@/lib/queries'
import { stepPath } from '@/lib/steps'
import { ChangeImpact } from './changes/ChangeImpact'
import { ReportChange } from './changes/ReportChange'
import { Chip } from './changes/ui'

/** Statuses of a change that is still being worked on (shown again after a reload). */
const OPEN: ChangeRequest['status'][] = ['ready', 'replanning', 'proposed']
const TOAST_MS = 3800

/** The step 7 screen: loading / error / locked states, then the change flow. */
export default function ChangesVersions() {
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const versions = usePlanVersions(projectId)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    if (!toast) return
    const t = window.setTimeout(() => setToast(null), TOAST_MS)
    return () => window.clearTimeout(t)
  }, [toast])

  let body: ReactNode
  if (versions.isPending) {
    body = (
      <div className="card center" role="status">
        <div className="ring" aria-hidden="true" />
        <p className="small muted">Loading plan versions…</p>
      </div>
    )
  } else if (versions.isError) {
    body = (
      <div className="alert bad" role="alert">
        <b>We couldn’t load this project’s plan versions.</b> {errorMessage(versions.error)}
        <div className="row mt8">
          <button className="btn sm sec" type="button" onClick={() => void versions.refetch()}>
            Try again
          </button>
        </div>
      </div>
    )
  } else if (versions.data.length === 0) {
    body = (
      <div id="chg-locked" className="card center">
        <h3 style={{ justifyContent: 'center' }}>No applied plan yet</h3>
        <p className="muted small">Apply a plan on step 6 first. Changes are measured against it.</p>
        <Link className="btn" to={stepPath('optimization', projectId)}>
          Go to Optimization Results
        </Link>
      </div>
    )
  } else {
    body = <ChangeFlow projectId={projectId} orgId={project.data?.orgId ?? null} versions={versions.data} onToast={setToast} />
  }

  return (
    <section className="view on" id="v-changes">
      <div className="pg">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; {project.data?.name ?? 'Project'} &rsaquo; Changes &amp; Versions
        </div>
        <h1>Changes &amp; Versions</h1>
        <div className="sub" style={{ marginBottom: 20 }}>
          Something changed? Bring it in here. NirnAIn shows what it affects, re-plans with as little disruption as possible, and keeps the current
          plan until you approve the new one.
        </div>
        {body}
      </div>
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

/**
 * `#chg-main`: report a change, the version list, and the active change. The active change
 * is the one just recorded, else the newest still-open one; after "Keep Plan vN" none is.
 */
function ChangeFlow({ projectId, orgId, versions, onToast }: { projectId: string; orgId: string | null; versions: PlanVersion[]; onToast: (msg: string) => void }) {
  const changes = useChanges(projectId)
  const [activeId, setActiveId] = useState<string | null>(null)
  // Re-plan run ids from C1's response, in case the change row isn't linked to its run yet.
  const [localRuns, setLocalRuns] = useState<Record<string, string>>({})
  const current = versions[versions.length - 1]
  const all = changes.data ?? []
  const active = activeId === null ? all.find((c) => OPEN.includes(c.status)) : all.find((c) => c.id === activeId)
  const runId = active && active.status !== 'rejected' ? (active.proposedRunId ?? localRuns[active.id] ?? null) : null
  const run = useRun(projectId, runId)
  const draft = Boolean(active && OPEN.includes(active.status) && isSolved(run.data?.status))

  return (
    <div id="chg-main">
      <div className="g2" style={{ gridTemplateColumns: '1.6fr 1fr' }}>
        <ReportChange projectId={projectId} orgId={orgId} current={current} onCreated={(c) => setActiveId(c.id)} />
        <div className="card">
          <h3>Versions</h3>
          <div id="versions">
            <VersionList versions={versions} draft={draft} />
          </div>
        </div>
      </div>
      {changes.isError && (
        <div className="alert bad mt" role="alert">
          We couldn’t load the changes recorded for this project. {errorMessage(changes.error)}{' '}
          <button className="btn sm sec" type="button" onClick={() => void changes.refetch()}>
            Try again
          </button>
        </div>
      )}
      {active && (
        <ChangeImpact
          key={active.id}
          projectId={projectId}
          change={active}
          current={current}
          run={run.data ?? null}
          runError={run.isError ? errorMessage(run.error) : null}
          onReplanned={(id) => setLocalRuns((prev) => ({ ...prev, [active.id]: id }))}
          onDecided={(decision) => {
            const base = active.baseVersion ?? current.version
            if (decision === 'reject') {
              setActiveId('')
              onToast(`Kept Plan v${base}. The change stays on record.`)
            } else {
              onToast(`Plan v${base + 1} approved. Dashboard, notifications and versions are updated.`)
            }
          }}
        />
      )}
    </div>
  )
}

/** Plan v1, v2… oldest first; the last one is in force. A solved, undecided re-plan shows as a draft. */
function VersionList({ versions, draft }: { versions: PlanVersion[]; draft: boolean }) {
  const last = versions[versions.length - 1]
  return (
    <>
      {versions.map((v) => (
        <div className="ver" key={v.runId}>
          <b>Plan v{v.version}</b> {v === last ? <Chip k="stated">In force</Chip> : <Chip k="default">Superseded</Chip>}
          <div className="small muted">
            Applied {formatDate(v.appliedAt)}
            {v.label && v.label !== `Plan v${v.version}` ? <> &middot; {v.label}</> : null}
            <br />
            Go-live {formatDate(v.completionDate)} &middot; {formatMoney(v.expectedCostCents)}
          </div>
        </div>
      ))}
      {draft && (
        <div className="ver">
          <b>Plan v{last.version + 1}</b> <Chip k="inferred">Draft</Chip>
          <div className="small muted">Waiting for your approval. Plan v{last.version} stays in force until then.</div>
        </div>
      )}
    </>
  )
}
