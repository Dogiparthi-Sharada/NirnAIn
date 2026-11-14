/**
 * The structured inputs of "Report a change" (step 7): direct edits and a progress update.
 *
 * Nothing here is parsed or guessed: the panes read the project's current inputs
 * (constraints, WBS, team), and send only the values that differ as `edits` to
 * POST /projects/{id}/changes through the `onSubmit` given by `ReportChange`.
 * Progress is a percent per work package; the backend turns it into the remaining duration.
 */
import { useMemo, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { useChangeTeam, type ChangeEdits, type PlanVersion } from '@/lib/changes'
import { formatDate, formatMoney } from '@/lib/format'
import { useConstraints, useWbs } from '@/lib/queries'
import type { WbsNode } from '@/lib/types'
import { DAYS_PER_WEEK, weeks } from './ui'

/** Work packages as the optimizer sees them: level-3 nodes and level-2 nodes without children. */
function workPackages(nodes: WbsNode[] | undefined): WbsNode[] {
  if (!nodes) return []
  const parents = new Set(nodes.map((n) => n.parentId).filter(Boolean))
  return nodes.filter((n) => n.level === 3 || (n.level === 2 && !parents.has(n.id)))
}

/** "$365,000", "365000" or "365k" → cents; null when empty, NaN when not a number. */
function parseDollars(raw: string): number | null {
  const clean = raw.replace(/[$,\s]/g, '')
  if (!clean) return null
  const thousands = /k$/i.test(clean)
  const value = Number(thousands ? clean.slice(0, -1) : clean)
  return Number.isFinite(value) && value >= 0 ? Math.round(value * (thousands ? 1000 : 1) * 100) : NaN
}

function InputsState({ loading, error, retry }: { loading: boolean; error: unknown; retry: () => void }) {
  if (loading) {
    return (
      <p className="small muted" role="status">
        Loading the project’s inputs…
      </p>
    )
  }
  return (
    <div className="alert bad" role="alert">
      We couldn’t load the project’s inputs. {errorMessage(error)}{' '}
      <button className="btn sm sec" type="button" onClick={retry}>
        Try again
      </button>
    </div>
  )
}

/** Direct edits. Only values that differ from the current inputs are sent. */
export function EditsPane({ projectId, current, busy, onSubmit }: { projectId: string; current: PlanVersion; busy: boolean; onSubmit: (edits: ChangeEdits) => void }) {
  const constraints = useConstraints(projectId)
  const wbs = useWbs(projectId)
  const team = useChangeTeam(projectId)
  const [budget, setBudget] = useState('')
  const [deadline, setDeadline] = useState('')
  const [hoursBy, setHoursBy] = useState<Record<string, string>>({})
  const [wpId, setWpId] = useState('')
  const [wpWeeks, setWpWeeks] = useState('')
  const packages = useMemo(() => workPackages(wbs.data), [wbs.data])

  if (!constraints.data || !wbs.data || !team.data) {
    const failed = constraints.isError || wbs.isError || team.isError
    return (
      <InputsState
        loading={!failed}
        error={constraints.error ?? wbs.error ?? team.error}
        retry={() => {
          void constraints.refetch()
          void wbs.refetch()
          void team.refetch()
        }}
      />
    )
  }
  const cs = constraints.data
  const budgetCents = parseDollars(budget)
  const wp = packages.find((p) => p.id === wpId)
  const wpDays = wpWeeks.trim() ? Math.round(Number(wpWeeks) * DAYS_PER_WEEK) : null

  const edits: ChangeEdits = {}
  if (budgetCents !== null && !Number.isNaN(budgetCents) && budgetCents !== cs.budgetTotalCents) edits.budgetTotalCents = budgetCents
  if (deadline && deadline !== cs.mustFinishBy) edits.mustFinishBy = deadline
  const hoursEdits: Record<string, number> = {}
  for (const p of team.data) {
    const raw = hoursBy[p.employeeId]?.trim()
    const h = raw ? Number(raw) : NaN
    if (Number.isFinite(h) && h >= 0 && h <= 80 && h !== p.hoursPerWeek) hoursEdits[p.employeeId] = h
  }
  if (Object.keys(hoursEdits).length) edits.hoursPerWeek = hoursEdits
  if (wp && wpDays !== null && Number.isFinite(wpDays) && wpDays >= 0 && wpDays !== wp.durationDays) edits.durationDays = { [wp.id]: wpDays }
  const invalid = (budgetCents !== null && Number.isNaN(budgetCents)) || (wpDays !== null && (!Number.isFinite(wpDays) || wpDays < 0))
  const was = { fontWeight: 400 }

  return (
    <>
      <div className="g2">
        <div className="field">
          <label htmlFor="ed-budget">
            Total budget ($) <span className="muted small" style={was}>was {formatMoney(cs.budgetTotalCents)} in Plan v{current.version}</span>
          </label>
          <input className="in" id="ed-budget" inputMode="decimal" value={budget} placeholder={cs.budgetTotalCents != null ? String(cs.budgetTotalCents / 100) : 'Not set'} onChange={(e) => setBudget(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ed-deadline">
            Must finish by <span className="muted small" style={was}>was {formatDate(cs.mustFinishBy)}</span>
          </label>
          <input className="in" id="ed-deadline" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </div>
      </div>
      <div className="field mt">
        <label htmlFor="ed-wp">A work package’s duration</label>
        <div className="row">
          <select className="in sm" id="ed-wp" value={wpId} onChange={(e) => setWpId(e.target.value)} style={{ maxWidth: 360 }}>
            <option value="">Choose a work package…</option>
            {packages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} {p.name} ({weeks(p.durationDays)})
              </option>
            ))}
          </select>
          <input className="in sm" aria-label="New duration in weeks" inputMode="decimal" style={{ width: 90 }} placeholder="weeks" value={wpWeeks} disabled={!wp} onChange={(e) => setWpWeeks(e.target.value)} />
          <span className="small muted">weeks</span>
        </div>
      </div>
      {team.data.length > 0 && (
        <div className="field mt">
          <label>Hours per week on this project</label>
          <table className="tbl">
            <thead>
              <tr>
                <th>Person</th>
                <th>Plan v{current.version}</th>
                <th>New</th>
              </tr>
            </thead>
            <tbody>
              {team.data.map((p) => (
                <tr key={p.employeeId}>
                  <td>{p.fullName}</td>
                  <td className="mono">{p.hoursPerWeek}h</td>
                  <td>
                    <input
                      className="in sm"
                      style={{ width: 80 }}
                      inputMode="decimal"
                      aria-label={`New hours per week for ${p.fullName}`}
                      placeholder={String(p.hoursPerWeek)}
                      value={hoursBy[p.employeeId] ?? ''}
                      onChange={(e) => setHoursBy((prev) => ({ ...prev, [p.employeeId]: e.target.value }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="hint">0 takes someone off the project.</div>
        </div>
      )}
      <div className="row end mt">
        {invalid && <span className="gate bad">Check the numbers above.</span>}
        <button className="btn" data-find="edits" type="button" disabled={busy || invalid || Object.keys(edits).length === 0} onClick={() => onSubmit(edits)}>
          Preview impact
        </button>
      </div>
    </>
  )
}

const PROGRESS_OPTIONS: [number, string][] = [
  [0, 'Not started / on track'],
  [25, '25% complete'],
  [50, '50% complete'],
  [75, '75% complete'],
  [100, 'Done'],
]

/** Progress per work package; only packages with some progress are sent. */
export function ProgressPane({ projectId, current, busy, onSubmit }: { projectId: string; current: PlanVersion; busy: boolean; onSubmit: (edits: ChangeEdits) => void }) {
  const wbs = useWbs(projectId)
  const [pct, setPct] = useState<Record<string, number>>({})
  const packages = useMemo(() => workPackages(wbs.data).filter((p) => (p.durationDays ?? 0) > 0), [wbs.data])
  if (!wbs.data) return <InputsState loading={!wbs.isError} error={wbs.error} retry={() => void wbs.refetch()} />
  if (packages.length === 0) return <p className="small muted">No work packages with a duration yet. Add them in WBS Builder.</p>
  const progress = Object.fromEntries(Object.entries(pct).filter(([, v]) => v > 0))

  return (
    <>
      <table className="tbl">
        <thead>
          <tr>
            <th>Work package</th>
            <th>Effort in Plan v{current.version}</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {packages.map((p) => (
            <tr key={p.id}>
              <td>
                <span className="mono">{p.code}</span> {p.name}
              </td>
              <td className="mono">{weeks(p.durationDays)}</td>
              <td>
                <select className="in sm" aria-label={`Progress of ${p.code} ${p.name}`} value={pct[p.id] ?? 0} onChange={(e) => setPct((prev) => ({ ...prev, [p.id]: Number(e.target.value) }))}>
                  {PROGRESS_OPTIONS.map(([v, label]) => (
                    <option key={v} value={v}>
                      {label}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hint">What is left becomes the remaining duration in the re-plan. Done work drops out of it.</div>
      <div className="row end mt">
        <button className="btn" data-find="progress" type="button" disabled={busy || Object.keys(progress).length === 0} onClick={() => onSubmit({ progress })}>
          Preview impact
        </button>
      </div>
    </>
  )
}
