/**
 * People joining the project in a step 7 "Edit inputs" change: the "+ Add person" form and the
 * list of newcomers the change will bring in.
 *
 * Why: after Plan v1 the team screen is read-only, so a mid-project hire, specialist or
 * contractor is recorded as a change, re-planned and approved here. How it fits: `EditsPane`
 * keeps the newcomers and sends them as `edits.addMembers` (POST /projects/{id}/changes); the
 * backend reserves an employee for each and adds them to the team when the change is approved.
 * The form reuses the Team screen's `.member-form` markup and checks what it can before adding
 * (name, role, no second person with the same name, whole hours 1–80, a contractor's rate),
 * listing every problem at once like the Team screen. Business rules (round 2): an employee is
 * costed from their company record, so only a contractor has a rate field; it may be left
 * empty when the project has a contractor rate, which then applies. People have no skills.
 */
import { useEffect, useRef, useState } from 'react'
import type { NewMember, WorkerType } from '@/lib/changes'
import { formatMoney } from '@/lib/format'
import { isWholeHours } from '@/lib/inputs'
import { memberSummary } from './ui'

const NEW_MEMBER_HOURS = '40'
const WORKER_TYPES: [WorkerType, string][] = [
  ['employee', 'Employee'],
  ['contractor', 'Contractor'],
]

/** "$175", "175.50" → cents; null when empty, NaN when not a positive amount. */
function rateCents(raw: string): number | null {
  const clean = raw.replace(/[$,\s]/g, '')
  if (!clean) return null
  const dollars = Number(clean)
  return Number.isFinite(dollars) && dollars > 0 && dollars <= 1_000_000 ? Math.round(dollars * 100) : NaN
}

/**
 * The add form. `takenNames` = everyone on the team plus newcomers already in this change;
 * `projectRateCents` = the project's contractor rate, the default for a contractor.
 */
export function NewcomerForm({
  takenNames,
  projectRateCents,
  onAdd,
  onCancel,
}: {
  takenNames: string[]
  projectRateCents: number | null
  onAdd: (member: NewMember) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [hours, setHours] = useState(NEW_MEMBER_HOURS)
  const [workerType, setWorkerType] = useState<WorkerType>('employee')
  const [rate, setRate] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  function add() {
    const fullName = name.trim().replace(/\s+/g, ' ')
    const roleTitle = role.trim()
    const hoursPerWeek = Number(hours)
    const contractor = workerType === 'contractor'
    const hourlyRateCents = contractor ? rateCents(rate) : null
    const found: string[] = []
    if (!fullName) found.push('Enter a name.')
    else if (takenNames.some((taken) => taken.toLowerCase() === fullName.toLowerCase())) found.push(`${fullName} is already on the team.`)
    if (!roleTitle) found.push('Enter a role.')
    if (hours.trim() === '' || !isWholeHours(hoursPerWeek)) found.push('Hours per week must be a whole number from 1 to 80.')
    if (contractor && Number.isNaN(hourlyRateCents)) found.push('The hourly rate must be an amount in dollars, e.g. 175.')
    else if (contractor && hourlyRateCents === null && projectRateCents === null) found.push('Enter the contractor’s hourly rate.')
    setProblems(found)
    if (found.length > 0) return
    onAdd({ fullName, roleTitle, hoursPerWeek, workerType, hourlyRateCents: contractor ? (hourlyRateCents ?? projectRateCents) : null })
  }

  return (
    <div className="member-form" id="chg-member-form">
      <h4>Add a person to the project</h4>
      <div className="alert bad" id="chg-mf-err" role="alert" hidden={problems.length === 0}>
        {problems.map((problem) => (
          <div key={problem}>{problem}</div>
        ))}
      </div>
      <div className="g3">
        <div className="field">
          <label htmlFor="chg-mf-name">
            Name<span className="req">*</span>
          </label>
          <input className="in" id="chg-mf-name" ref={nameRef} maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="chg-mf-role">
            Role<span className="req">*</span>
          </label>
          <input className="in" id="chg-mf-role" maxLength={120} placeholder="e.g. Security Specialist" value={role} onChange={(event) => setRole(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="chg-mf-hours">
            Hours per week on this project<span className="req">*</span>
          </label>
          <input className="in" id="chg-mf-hours" type="number" min={1} max={80} step={1} value={hours} onChange={(event) => setHours(event.target.value)} />
        </div>
      </div>
      <div className="g2 mt">
        <div className="field">
          <label id="chg-mf-type-label">Type</label>
          <span className="seg" role="group" aria-labelledby="chg-mf-type-label">
            {WORKER_TYPES.map(([value, label]) => (
              <button key={value} type="button" className={workerType === value ? 'on' : ''} aria-pressed={workerType === value} onClick={() => setWorkerType(value)}>
                {label}
              </button>
            ))}
          </span>
          <div className="hint">{workerType === 'employee' ? 'Costed at their company rate, or the standard rate.' : 'Hired in at their own hourly rate.'}</div>
        </div>
        {workerType === 'contractor' && (
          <div className="field">
            <label htmlFor="chg-mf-rate">
              Rate ($/h){projectRateCents === null && <span className="req">*</span>}
            </label>
            <input
              className="in"
              id="chg-mf-rate"
              inputMode="decimal"
              placeholder={projectRateCents === null ? 'e.g. 175' : `Project rate ${formatMoney(projectRateCents)}`}
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          </div>
        )}
      </div>
      <div className="row mt">
        <button className="btn sm" id="chg-mf-add" type="button" onClick={add}>
          Add to this change
        </button>
        <button className="btn sec sm" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/** Newcomers this change brings in, each with a button to leave them out again. */
export function NewcomerList({ newcomers, onDrop }: { newcomers: NewMember[]; onDrop: (fullName: string) => void }) {
  if (newcomers.length === 0) return null
  return (
    <table className="tbl mt" aria-label="People joining">
      <tbody>
        {newcomers.map((member) => (
          <tr key={member.fullName}>
            <td>
              <b>Joins:</b> {memberSummary(member)}
            </td>
            <td style={{ textAlign: 'right' }}>
              <button className="btn sm sec" type="button" aria-label={`Don’t add ${member.fullName}`} onClick={() => onDrop(member.fullName)}>
                Don’t add
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
