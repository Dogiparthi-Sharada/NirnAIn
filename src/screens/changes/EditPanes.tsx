/**
 * The structured inputs of "Report a change" (step 7): direct edits and a progress update.
 *
 * Nothing here is parsed or guessed: the panes read the project's current inputs
 * (constraints, WBS, team), and send only the values that differ as `edits` to
 * POST /projects/{id}/changes through the `onSubmit` given by `ReportChange`. People can
 * join ("+ Add person", `TeamEdits.tsx`) or leave ("Remove") the project mid-way.
 * Progress is a percent per work package; the backend turns it into the remaining duration.
 */
import { useMemo, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { useChangeTeam, type ChangeEdits, type NewMember, type PlanVersion } from '@/lib/changes'
import { formatDate, formatMoney } from '@/lib/format'
import { useConstraints, useWbs } from '@/lib/queries'
import type { WbsNode } from '@/lib/types'
import { NewcomerForm, NewcomerList } from './TeamEdits'
import { DAYS_PER_WEEK, weeks } from './ui'

// At most this many people join in one change (the server's limit).
const MAX_NEWCOMERS = 10

/** Work packages as the optimizer sees them: level-3 nodes and level-2 nodes without children. */
function workPackages(nodes: WbsNode[] | undefined): WbsNode[] {
  if (!nodes) return []
  const parents = new Set(nodes.map((node) => node.parentId).filter(Boolean))
  return nodes.filter((node) => node.level === 3 || (node.level === 2 && !parents.has(node.id)))
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
  const [workPackageId, setWorkPackageId] = useState('')
  const [workPackageWeeks, setWorkPackageWeeks] = useState('')
  const [newcomers, setNewcomers] = useState<NewMember[]>([])
  const [leaving, setLeaving] = useState<Set<string>>(() => new Set())
  const [adding, setAdding] = useState(false)
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
  const constraintSet = constraints.data
  const budgetCents = parseDollars(budget)
  const workPackage = packages.find((candidate) => candidate.id === workPackageId)
  const workPackageDays = workPackageWeeks.trim() ? Math.round(Number(workPackageWeeks) * DAYS_PER_WEEK) : null

  const edits: ChangeEdits = {}
  if (budgetCents !== null && !Number.isNaN(budgetCents) && budgetCents !== constraintSet.budgetTotalCents) edits.budgetTotalCents = budgetCents
  if (deadline && deadline !== constraintSet.mustFinishBy) edits.mustFinishBy = deadline
  const hoursEdits: Record<string, number> = {}
  for (const member of team.data) {
    if (leaving.has(member.employeeId)) continue
    const raw = hoursBy[member.employeeId]?.trim()
    const hoursValue = raw ? Number(raw) : NaN
    if (Number.isFinite(hoursValue) && hoursValue >= 0 && hoursValue <= 80 && hoursValue !== member.hoursPerWeek) hoursEdits[member.employeeId] = hoursValue
  }
  if (Object.keys(hoursEdits).length) edits.hoursPerWeek = hoursEdits
  if (newcomers.length) edits.addMembers = newcomers
  if (leaving.size) edits.removeMembers = [...leaving]
  // Business rule (the server checks it too): a plan needs at least one person.
  const nobodyLeft = leaving.size > 0 && team.data.length - leaving.size + newcomers.length < 1
  const toggleLeaving = (employeeId: string) =>
    setLeaving((previous) => {
      const next = new Set(previous)
      if (next.has(employeeId)) next.delete(employeeId)
      else next.add(employeeId)
      return next
    })
  if (workPackage && workPackageDays !== null && Number.isFinite(workPackageDays) && workPackageDays >= 0 && workPackageDays !== workPackage.durationDays) edits.durationDays = { [workPackage.id]: workPackageDays }
  const invalid = nobodyLeft || (budgetCents !== null && Number.isNaN(budgetCents)) || (workPackageDays !== null && (!Number.isFinite(workPackageDays) || workPackageDays < 0))
  const was = { fontWeight: 400 }

  return (
    <>
      <div className="g2">
        <div className="field">
          <label htmlFor="ed-budget">
            Total budget ($) <span className="muted small" style={was}>was {formatMoney(constraintSet.budgetTotalCents)} in Plan v{current.version}</span>
          </label>
          <input className="in" id="ed-budget" inputMode="decimal" value={budget} placeholder={constraintSet.budgetTotalCents != null ? String(constraintSet.budgetTotalCents / 100) : 'Not set'} onChange={(event) => setBudget(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ed-deadline">
            Must finish by <span className="muted small" style={was}>was {formatDate(constraintSet.mustFinishBy)}</span>
          </label>
          <input className="in" id="ed-deadline" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} />
        </div>
      </div>
      <div className="field mt">
        <label htmlFor="ed-work-package">A work package’s duration</label>
        <div className="row">
          <select className="in sm" id="ed-work-package" value={workPackageId} onChange={(event) => setWorkPackageId(event.target.value)} style={{ maxWidth: 360 }}>
            <option value="">Choose a work package…</option>
            {packages.map((choice) => (
              <option key={choice.id} value={choice.id}>
                {choice.code} {choice.name} ({weeks(choice.durationDays)})
              </option>
            ))}
          </select>
          <input className="in sm" aria-label="New duration in weeks" inputMode="decimal" style={{ width: 90 }} placeholder="weeks" value={workPackageWeeks} disabled={!workPackage} onChange={(event) => setWorkPackageWeeks(event.target.value)} />
          <span className="small muted">weeks</span>
        </div>
      </div>
      <div className="field mt">
        <label>Team: hours per week on this project</label>
        {team.data.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Person</th>
                <th>Plan v{current.version}</th>
                <th>New</th>
                <th>
                  <span className="sr-only">On the project</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {team.data.map((member) => {
                const leaves = leaving.has(member.employeeId)
                return (
                  <tr key={member.employeeId}>
                    <td>
                      {member.fullName}
                      {leaves && <span className="small muted"> · leaves the project</span>}
                    </td>
                    <td className="mono">{member.hoursPerWeek}h</td>
                    <td>
                      <input
                        className="in sm"
                        style={{ width: 80 }}
                        inputMode="decimal"
                        aria-label={`New hours per week for ${member.fullName}`}
                        placeholder={String(member.hoursPerWeek)}
                        disabled={leaves}
                        value={leaves ? '' : (hoursBy[member.employeeId] ?? '')}
                        onChange={(event) => setHoursBy((previous) => ({ ...previous, [member.employeeId]: event.target.value }))}
                      />
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className={`btn sm sec${leaves ? '' : ' danger'}`}
                        type="button"
                        aria-pressed={leaves}
                        aria-label={leaves ? `Keep ${member.fullName} on the project` : `Remove ${member.fullName} from the project`}
                        onClick={() => toggleLeaving(member.employeeId)}
                      >
                        {leaves ? 'Keep' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        <div className="hint">0 hours or Remove takes someone off the project; the re-plan gives their work to others.</div>
        {nobodyLeft && (
          <div className="note bad" role="alert">
            Keep at least one person on the project, or add someone first.
          </div>
        )}
        <NewcomerList newcomers={newcomers} onDrop={(fullName) => setNewcomers((previous) => previous.filter((member) => member.fullName !== fullName))} />
        {adding ? (
          <NewcomerForm
            projectId={projectId}
            takenNames={[...team.data.map((member) => member.fullName), ...newcomers.map((member) => member.fullName)]}
            onAdd={(member) => {
              setNewcomers((previous) => [...previous, member])
              setAdding(false)
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <button className="btn sm sec mt8" id="chg-add-person" type="button" disabled={newcomers.length >= MAX_NEWCOMERS} onClick={() => setAdding(true)}>
            + Add person
          </button>
        )}
      </div>
      <div className="row end mt">
        {invalid && <span className="gate bad">{nobodyLeft ? 'A plan needs at least one person.' : 'Check the numbers above.'}</span>}
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
  const [percentById, setPct] = useState<Record<string, number>>({})
  const packages = useMemo(() => workPackages(wbs.data).filter((workPackage) => (workPackage.durationDays ?? 0) > 0), [wbs.data])
  if (!wbs.data) return <InputsState loading={!wbs.isError} error={wbs.error} retry={() => void wbs.refetch()} />
  if (packages.length === 0) return <p className="small muted">No work packages with a duration yet. Add them in WBS Builder.</p>
  const progress = Object.fromEntries(Object.entries(percentById).filter(([, percent]) => percent > 0))

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
          {packages.map((workPackage) => (
            <tr key={workPackage.id}>
              <td>
                <span className="mono">{workPackage.code}</span> {workPackage.name}
              </td>
              <td className="mono">{weeks(workPackage.durationDays)}</td>
              <td>
                <select className="in sm" aria-label={`Progress of ${workPackage.code} ${workPackage.name}`} value={percentById[workPackage.id] ?? 0} onChange={(event) => setPct((previous) => ({ ...previous, [workPackage.id]: Number(event.target.value) }))}>
                  {PROGRESS_OPTIONS.map(([percent, label]) => (
                    <option key={percent} value={percent}>
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
