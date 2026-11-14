/**
 * Step 7, Changes & Versions: record what changed after a plan was applied, re-plan and approve the next version.
 *
 * Purpose: New meeting notes, direct edits or a progress update are analysed into what they
 * affect; NirnAIn re-plans with as little disruption as possible against the plan in force and
 * keeps Plan vN in force until Plan vN+1 is approved. The step is locked until a plan is applied.
 * How it fits: Port of the mockup's views/changes.js, changes-impact.js and changes-compare.js;
 * route `projects/:projectId/changes` in `App.tsx`; data from `lib/changes.ts`, panels in
 * `screens/changes/*`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { errorMessage } from '@/lib/api'
import { isSolved, useChanges, usePlanVersions, useRun, type ChangeRequest, type PlanVersion } from '@/lib/changes'
import { formatDate, formatDateTime, formatMoney } from '@/lib/format'
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
    const timer = window.setTimeout(() => setToast(null), TOAST_MS)
    return () => window.clearTimeout(timer)
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
 * `#chg-main`: report a change, the version list, the open changes and the active change. The
 * active change is the one just recorded or picked, else the newest still-open one; after
 * "Keep Plan vN" none is. Every open change stays reachable from the list (QA NEW-7f).
 */
function ChangeFlow({ projectId, orgId, versions, onToast }: { projectId: string; orgId: string | null; versions: PlanVersion[]; onToast: (message: string) => void }) {
  const changes = useChanges(projectId)
  const [activeId, setActiveId] = useState<string | null>(null)
  // Re-plan run ids from C1's response, in case the change row isn't linked to its run yet.
  const [localRuns, setLocalRuns] = useState<Record<string, string>>({})
  const current = versions[versions.length - 1]
  const all = changes.data ?? []
  const open = all.filter((change) => OPEN.includes(change.status))
  const active = activeId === null ? open[0] : all.find((change) => change.id === activeId)
  const runId = active && active.status !== 'rejected' ? (active.proposedRunId ?? localRuns[active.id] ?? null) : null
  const run = useRun(projectId, runId)
  const draft = Boolean(active && OPEN.includes(active.status) && isSolved(run.data?.status))

  return (
    <div id="chg-main">
      <div className="banner" id="chg-editable" role="status">
        Steps 3–5 stay editable while Plan v{current.version} is in force: changes there take effect when you re-run on step 6 and apply Plan v
        {current.version + 1}. Use this page to record why the plan changed (meeting notes, edits or progress) and compare before you approve.
      </div>
      <div className="g2" style={{ gridTemplateColumns: '1.6fr 1fr' }}>
        <ReportChange projectId={projectId} orgId={orgId} current={current} onCreated={(change) => setActiveId(change.id)} />
        <div className="card">
          <h3>Versions</h3>
          <div id="versions">
            <VersionList versions={versions} draft={draft} />
          </div>
        </div>
      </div>
      <OpenChanges changes={open} activeId={active?.id ?? null} onOpen={setActiveId} />
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
          onReplanned={(id) => setLocalRuns((previous) => ({ ...previous, [active.id]: id }))}
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

const SOURCE_LABELS: Record<ChangeRequest['source'], string> = {
  notes: 'Meeting notes',
  edits: 'Edited inputs',
  progress: 'Progress update',
}
const STATUS_LABELS: Partial<Record<ChangeRequest['status'], string>> = {
  ready: 'not re-planned yet',
  replanning: 're-planning',
  proposed: 're-planned, waiting for your decision',
}

/**
 * The changes still open (recorded, re-planning or re-planned but not decided), newest first.
 * Shown when there is one besides the change on screen, so each can be opened and decided.
 */
function OpenChanges({ changes, activeId, onOpen }: { changes: ChangeRequest[]; activeId: string | null; onOpen: (id: string) => void }) {
  if (!changes.some((change) => change.id !== activeId)) return null
  return (
    <div className="card mt" id="chg-open">
      <h3>Open changes ({changes.length})</h3>
      <p className="small muted" style={{ marginTop: 0 }}>
        Each stays open until you approve its plan or keep the current one. Open one to see its impact.
      </p>
      {changes.map((change) => {
        const found = change.impact?.changes.length ?? 0
        const shown = change.id === activeId
        return (
          <div className="ver row between" key={change.id}>
            <div>
              <b>{SOURCE_LABELS[change.source]}</b> &middot; {formatDateTime(change.createdAt)}
              <div className="small muted">
                {found} change{found === 1 ? '' : 's'} &middot; {STATUS_LABELS[change.status] ?? change.status}
              </div>
            </div>
            {shown ? (
              <Chip kind="stated">Shown below</Chip>
            ) : (
              <button className="btn sm sec" type="button" onClick={() => onOpen(change.id)}>
                Open
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** Plan v1, v2… oldest first; the last one is in force. A solved, undecided re-plan shows as a draft. */
function VersionList({ versions, draft }: { versions: PlanVersion[]; draft: boolean }) {
  const last = versions[versions.length - 1]
  return (
    <>
      {versions.map((version) => (
        <div className="ver" key={version.runId}>
          <b>Plan v{version.version}</b> {version === last ? <Chip kind="stated">In force</Chip> : <Chip kind="default">Superseded</Chip>}
          <div className="small muted">
            Applied {formatDate(version.appliedAt)}
            {version.label && version.label !== `Plan v${version.version}` ? <> &middot; {version.label}</> : null}
            <br />
            Go-live {formatDate(version.completionDate)} &middot; {formatMoney(version.expectedCostCents)}
          </div>
        </div>
      ))}
      {draft && (
        <div className="ver">
          <b>Plan v{last.version + 1}</b> <Chip kind="inferred">Draft</Chip>
          <div className="small muted">Waiting for your approval. Plan v{last.version} stays in force until then.</div>
        </div>
      )}
    </>
  )
}
