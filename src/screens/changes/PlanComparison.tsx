/**
 * Plan vN+1 against Plan vN on step 7: KPIs side by side with deltas, the Gantt with the
 * in-force plan dashed underneath, what moved and why, and the decision buttons.
 *
 * Data comes from GET /changes/{id}/compare (rows per work package, both plans' cost,
 * finish date and budget). "Approve" applies the proposed run (it becomes the next
 * version and the change's values become the project's inputs); "Keep" rejects the
 * change and Plan vN stays in force.
 */
import type { ChangeComparison, CompareRow } from '@/lib/changes'
import { errorMessage } from '@/lib/api'
import { formatDate, formatMoney } from '@/lib/format'
import { PlanGantt } from './PlanGantt'
import { Chip, DAY_MS, day, range, signedWeeks } from './ui'

interface CompareQuery {
  data?: ChangeComparison
  isPending: boolean
  isError: boolean
  error: unknown
  refetch: () => unknown
}

/** A row the user should see in "What moved and why": added, dropped, re-dated or re-owned. */
function isChanged(r: CompareRow): boolean {
  if (r.before === null || r.after === null || r.ownerChanged) return true
  return r.before.start !== r.after.start || r.before.end !== r.after.end
}

/** The comparison card stack plus Approve / Keep. Loading and error states retry the comparison only. */
export function PlanComparison({
  query,
  baseVersion,
  nextVersion,
  approved,
  busy,
  error,
  onApprove,
  onKeep,
}: {
  query: CompareQuery
  baseVersion: number
  nextVersion: number
  approved: boolean
  busy: boolean
  error: string | null
  onApprove: () => void
  onKeep: () => void
}) {
  if (query.isPending) {
    return (
      <div className="card center" role="status">
        <div className="ring" aria-hidden="true" />
        <p className="small muted">
          Comparing Plan v{nextVersion} with Plan v{baseVersion}…
        </p>
      </div>
    )
  }
  if (query.isError || !query.data) {
    return (
      <div className="alert bad" role="alert">
        We couldn’t compare the plans. {errorMessage(query.error)}{' '}
        <button className="btn sm sec" type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    )
  }

  const { base, proposed, rows } = query.data
  const changed = rows.filter(isChanged)
  const moved = changed.filter((r) => r.before && r.after && (r.before.start !== r.after.start || r.before.end !== r.after.end)).length
  const owners = rows.filter((r) => r.ownerChanged).length
  const added = rows.filter((r) => r.before === null).length
  const gone = rows.filter((r) => r.after === null).length
  const unchanged = rows.length - changed.length

  const c1 = base.expectedCostCents
  const c2 = proposed.expectedCostCents
  const costDelta = c1 != null && c2 != null ? c2 - c1 : null
  const h1 = base.budgetCents != null && c1 != null ? base.budgetCents - c1 : null
  const h2 = proposed.budgetCents != null && c2 != null ? proposed.budgetCents - c2 : null
  const finishDelta =
    proposed.completionDate && base.completionDate ? Math.round((day(proposed.completionDate) - day(base.completionDate)) / DAY_MS) : null

  let finishNote = '—'
  if (finishDelta === 0) finishNote = 'Unchanged'
  else if (finishDelta !== null) finishNote = `${signedWeeks(finishDelta)} vs Plan v${baseVersion}`
  let costNote = '—'
  if (costDelta === 0) costNote = `Same as v${baseVersion}`
  else if (costDelta !== null) costNote = `${formatMoney(Math.abs(costDelta))} ${costDelta > 0 ? 'more' : 'less'} than v${baseVersion}`

  return (
    <>
      <div className="card">
        <div className="row between">
          <h3 style={{ margin: 0 }}>
            Plan v{nextVersion} vs Plan v{baseVersion}
          </h3>
          {approved ? <Chip k="stated">Approved · in force</Chip> : <Chip k="inferred">Draft · not applied yet</Chip>}
        </div>
        <div className="kpis mt" style={{ marginBottom: 0 }}>
          <Kpi k="Go-live" v={formatDate(proposed.completionDate)} s={finishNote} />
          <Kpi k="Total cost" v={formatMoney(c2)} s={costNote} />
          <Kpi
            k="Budget headroom"
            v={formatMoney(h2)}
            s={proposed.budgetCents == null ? 'No budget set' : `under ${formatMoney(proposed.budgetCents)} · was ${formatMoney(h1)}`}
          />
          <Kpi k="What changed" v={`${moved} moved`} s={`${owners} owner${owners === 1 ? '' : 's'} changed · ${added} added · ${gone} done or dropped`} />
        </div>
      </div>
      <div className="card">
        <h3>
          Timeline{' '}
          <span className="small muted" style={{ fontWeight: 400 }}>
            dashed = Plan v{baseVersion}, solid = Plan v{nextVersion}, lighter red = moved
          </span>
        </h3>
        <PlanGantt rows={rows} deadline={proposed.mustFinishBy} />
      </div>
      <div className="card">
        <h3>What moved and why</h3>
        {changed.length === 0 ? (
          <p className="small muted">Nothing moved: every work package keeps its dates and owner.</p>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>WBS</th>
                <th>Work package</th>
                <th>Plan v{baseVersion}</th>
                <th>Plan v{nextVersion}</th>
                <th>Change</th>
                <th>Owner</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {changed.map((r) => (
                <MovedRow key={r.wbsNodeId} r={r} />
              ))}
              {unchanged > 0 && (
                <tr>
                  <td className="mono">—</td>
                  <td>Everything else</td>
                  <td colSpan={5} className="small muted">
                    {unchanged} work package{unchanged === 1 ? '' : 's'} unchanged.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      {error && (
        <div className="alert bad" role="alert">
          {error}
        </div>
      )}
      <div className="row end">
        {!approved && (
          <button className="btn sec" id="btn-keep-v1" type="button" disabled={busy} onClick={onKeep}>
            Keep Plan v{baseVersion}
          </button>
        )}
        <button className="btn" id="btn-approve-v2" type="button" disabled={busy || approved} onClick={onApprove}>
          {approved ? `✓ Plan v${nextVersion} approved` : busy ? 'Applying…' : `Approve Plan v${nextVersion}`}
        </button>
      </div>
    </>
  )
}

function Kpi({ k, v, s }: { k: string; v: string; s: string }) {
  return (
    <div className="kpi">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      <div className="s">{s}</div>
    </div>
  )
}

function MovedRow({ r }: { r: CompareRow }) {
  let change: string
  if (r.before === null) change = 'Added'
  else if (r.after === null) change = r.why?.startsWith('Done') ? 'Done' : 'Dropped'
  else if (r.shiftDays) change = signedWeeks(r.shiftDays)
  else change = r.ownerChanged ? 'Owner' : 'Start moved'
  let owner = r.before?.owner ?? '—'
  if (r.ownerChanged) owner = `${r.before?.owner ?? '—'} → ${r.after?.owner ?? '—'}`
  else if (r.after?.owner) owner = `${r.after.owner} (same)`
  return (
    <tr>
      <td className="mono">{r.code}</td>
      <td>{r.name}</td>
      <td className="small">{range(r.before)}</td>
      <td className="small">
        <b>{range(r.after)}</b>
      </td>
      <td className="mono">{change}</td>
      <td className="small">{owner}</td>
      <td className="small">{r.why ?? '—'}</td>
    </tr>
  )
}
