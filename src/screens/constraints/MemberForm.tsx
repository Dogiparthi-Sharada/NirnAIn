/**
 * The add / edit form under the team table on step 5 (approved mockup views/constraints-team.js
 * `openMemberForm` / `saveMember`): name, role, type (employee or contractor), hours per week on
 * this project and, for contractors only, the hourly rate. (Skills are not part of planning, so
 * the design's skill picks are left out.)
 *
 * Cost rule: an employee's cost comes from their company record (else the standard $85/h), so the
 * form asks no rate for them; a contractor's rate is required, unless the project has a confirmed
 * contractor rate, which then applies when the field is left blank.
 *
 * The form checks what it can before saving (a name, a role, no second person with the same name,
 * hours a whole number from 1 to 80, a contractor's rate in dollars) and lists every problem at
 * once; the server's answer (for example a 409 duplicate) is shown in the same box.
 */
import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { parseDollars } from '@/lib/constraints'
import {
  DEFAULT_HOURLY_RATE_CENTS,
  isHourlyRate,
  isWholeHours,
  perHourText,
  workerTypeOf,
  type ProjectTeamMember,
  type TeamMemberInput,
  type WorkerType,
} from '@/lib/inputs'

const NEW_MEMBER_HOURS = '40'

/** The add / edit form. `member` null = a new person. */
export function MemberForm({ member, team, projectContractorRateCents, saving, serverError, onSave, onCancel }: {
  member: ProjectTeamMember | null
  team: ProjectTeamMember[]
  /** The project's confirmed contractor rate, the default for a contractor without one; null = none. */
  projectContractorRateCents: number | null
  saving: boolean
  serverError: unknown
  onSave: (input: TeamMemberInput) => void
  onCancel: () => void
}) {
  const startingType = member ? workerTypeOf(member) : 'employee'
  const [name, setName] = useState(member?.fullName ?? '')
  const [role, setRole] = useState(member?.roleTitle ?? '')
  const [workerType, setWorkerType] = useState<WorkerType>(startingType)
  const [hours, setHours] = useState(member ? String(member.hoursPerWeek) : NEW_MEMBER_HOURS)
  const [rate, setRate] = useState(startingType === 'contractor' && member?.hourlyRateCents != null ? String(member.hourlyRateCents / 100) : '')
  const [problems, setProblems] = useState<string[]>([])
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  const contractor = workerType === 'contractor'

  function save() {
    const fullName = name.trim()
    const roleTitle = role.trim()
    const hoursPerWeek = Number(hours)
    const found: string[] = []
    if (!fullName) found.push('Enter a name.')
    else if (team.some((other) => other.employeeId !== member?.employeeId && other.fullName.toLowerCase() === fullName.toLowerCase())) {
      found.push(`${fullName} is already on the team.`)
    }
    if (!roleTitle) found.push('Enter a role.')
    if (hours.trim() === '' || !isWholeHours(hoursPerWeek)) found.push('Hours per week must be a whole number from 1 to 80.')
    const hourlyRateCents = contractor ? parseDollars(rate) : null
    if (contractor && hourlyRateCents === null && projectContractorRateCents === null) found.push('Enter the contractor\u2019s hourly rate.')
    else if (hourlyRateCents !== null && !isHourlyRate(hourlyRateCents)) found.push('Rate must be dollars per hour, for example 95.')
    setProblems(found)
    if (found.length > 0) return
    onSave(contractor ? { fullName, roleTitle, hoursPerWeek, workerType, hourlyRateCents } : { fullName, roleTitle, hoursPerWeek, workerType })
  }

  const messages = serverError && problems.length === 0 ? [errorMessage(serverError)] : problems

  return (
    <div className="member-form" id="member-form">
      <h4>{member ? `Edit ${member.fullName}` : 'Add a team member'}</h4>
      <div className="alert bad" id="mf-err" role="alert" hidden={messages.length === 0}>
        {messages.map((message) => (
          <div key={message}>{message}</div>
        ))}
      </div>
      <div className="g4">
        <div className="field">
          <label htmlFor="mf-name">
            Name<span className="req">*</span>
          </label>
          <input className="in" id="mf-name" ref={nameRef} maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mf-role">
            Role<span className="req">*</span>
          </label>
          <input
            className="in"
            id="mf-role"
            list="role-list"
            maxLength={60}
            placeholder="Pick or type a role"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="mf-type">
            Type<span className="req">*</span>
          </label>
          <select
            className="in"
            id="mf-type"
            value={workerType}
            aria-describedby="mf-type-note"
            onChange={(event) => setWorkerType(event.target.value as WorkerType)}
          >
            <option value="employee">Employee</option>
            <option value="contractor">Contractor</option>
          </select>
          <p className="note" id="mf-type-note">
            {contractor
              ? 'Billed at their own hourly rate, overtime included.'
              : `Costed at their company rate, or the standard ${perHourText(DEFAULT_HOURLY_RATE_CENTS)} if none is on record.`}
          </p>
        </div>
        <div className="field">
          <label htmlFor="mf-hours">
            Hours per week on this project<span className="req">*</span>
          </label>
          <input className="in" id="mf-hours" type="number" min={1} max={80} step={1} value={hours} onChange={(event) => setHours(event.target.value)} />
        </div>
        {contractor && (
          <div className="field">
            <label htmlFor="mf-rate">
              Rate ($/h)
              {projectContractorRateCents === null && <span className="req">*</span>}
            </label>
            <input
              className="in"
              id="mf-rate"
              inputMode="decimal"
              placeholder={projectContractorRateCents === null ? 'e.g. 150' : `project rate ${perHourText(projectContractorRateCents)}`}
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          </div>
        )}
      </div>
      <div className="row mt">
        <button className="btn sm" id="mf-save" type="button" onClick={save} disabled={saving} aria-busy={saving || undefined}>
          {saving ? 'Saving\u2026' : member ? 'Save changes' : 'Add to team'}
        </button>
        <button className="btn sec sm" id="mf-cancel" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}
