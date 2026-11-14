/**
 * People joining the project in a step 7 "Edit inputs" change: the "+ Add person" form and the
 * list of newcomers the change will bring in.
 *
 * Why: after Plan v1 the team screen is read-only, so a mid-project hire, specialist or
 * contractor is recorded as a change, re-planned and approved here. How it fits: `EditsPane`
 * keeps the newcomers and sends them as `edits.addMembers` (POST /projects/{id}/changes); the
 * backend reserves an employee for each and adds them to the team when the change is approved.
 * The form reuses the Team screen's `.member-form` / `.skill-picks` markup and checks what it
 * can before adding (name, role, no second person with the same name, whole hours 1–80, rate),
 * listing every problem at once like the Team screen.
 */
import { useEffect, useRef, useState } from 'react'
import type { NewMember } from '@/lib/changes'
import { isWholeHours, useProjectSkills } from '@/lib/inputs'
import { memberSummary } from './ui'

const NEW_MEMBER_HOURS = '40'

/** "$175", "175.50" → cents; null when empty, NaN when not a non-negative amount. */
function rateCents(raw: string): number | null {
  const clean = raw.replace(/[$,\s]/g, '')
  if (!clean) return null
  const dollars = Number(clean)
  return Number.isFinite(dollars) && dollars >= 0 ? Math.round(dollars * 100) : NaN
}

/** The add form. `takenNames` = everyone on the team plus newcomers already in this change. */
export function NewcomerForm({
  projectId,
  takenNames,
  onAdd,
  onCancel,
}: {
  projectId: string
  takenNames: string[]
  onAdd: (member: NewMember) => void
  onCancel: () => void
}) {
  const skills = useProjectSkills(projectId)
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [hours, setHours] = useState(NEW_MEMBER_HOURS)
  const [rate, setRate] = useState('')
  const [contractor, setContractor] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(() => new Set())
  const [problems, setProblems] = useState<string[]>([])
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  function togglePick(skillName: string) {
    setPicked((previous) => {
      const next = new Set(previous)
      if (next.has(skillName)) next.delete(skillName)
      else next.add(skillName)
      return next
    })
  }

  function add() {
    const fullName = name.trim().replace(/\s+/g, ' ')
    const roleTitle = role.trim()
    const hoursPerWeek = Number(hours)
    const hourlyRateCents = rateCents(rate)
    const found: string[] = []
    if (!fullName) found.push('Enter a name.')
    else if (takenNames.some((taken) => taken.toLowerCase() === fullName.toLowerCase())) found.push(`${fullName} is already on the team.`)
    if (!roleTitle) found.push('Enter a role.')
    if (hours.trim() === '' || !isWholeHours(hoursPerWeek)) found.push('Hours per week must be a whole number from 1 to 80.')
    if (Number.isNaN(hourlyRateCents)) found.push('The hourly rate must be an amount in dollars, or empty.')
    setProblems(found)
    if (found.length > 0) return
    const chosen = (skills.data ?? []).map((skill) => skill.name).filter((skillName) => picked.has(skillName))
    onAdd({ fullName, roleTitle, hoursPerWeek, skills: chosen, hourlyRateCents, isContractor: contractor })
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
          <label htmlFor="chg-mf-rate">
            Hourly rate ($) <span className="muted small">optional</span>
          </label>
          <input className="in" id="chg-mf-rate" inputMode="decimal" placeholder="Team average if empty" value={rate} onChange={(event) => setRate(event.target.value)} />
        </div>
        <div className="field">
          <label id="chg-mf-contract-label">Contract</label>
          <div className="skill-picks" role="group" aria-labelledby="chg-mf-contract-label">
            <label className="pick">
              <input type="checkbox" checked={contractor} onChange={(event) => setContractor(event.target.checked)} /> Outside contractor
            </label>
          </div>
        </div>
      </div>
      <div className="field mt">
        <label id="chg-mf-skills-label">Skills</label>
        <div className="skill-picks" role="group" aria-labelledby="chg-mf-skills-label">
          {skills.isPending ? (
            <span className="small muted">Loading skills&hellip;</span>
          ) : skills.isError ? (
            <span className="small muted">We couldn’t load the project’s skills; you can add the person without them.</span>
          ) : skills.data.length > 0 ? (
            skills.data.map((skill) => (
              <label className="pick" key={skill.skillId}>
                <input type="checkbox" value={skill.name} checked={picked.has(skill.name)} onChange={() => togglePick(skill.name)} /> {skill.name}
              </label>
            ))
          ) : (
            <span className="small muted">The project has no skill list.</span>
          )}
        </div>
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
              {member.skills.length > 0 && <span className="small muted"> · {member.skills.join(', ')}</span>}
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
