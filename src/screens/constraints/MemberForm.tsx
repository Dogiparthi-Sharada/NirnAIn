/**
 * The add / edit form under the team table on step 5 (approved mockup views/constraints-team.js
 * `openMemberForm` / `saveMember`): name, role, hours per week on this project and skills.
 *
 * The form checks what it can before saving (a name, a role, no second person with the same name,
 * hours a whole number from 1 to 80) and lists every problem at once; the server's answer (for
 * example a 409 duplicate or the plan lock) is shown in the same box. Skills are picked from the
 * project's skill list; skills a person has outside that list are kept when they are edited.
 */
import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { isWholeHours, type ProjectSkill, type ProjectTeamMember, type TeamMemberInput } from '@/lib/inputs'

const NEW_MEMBER_HOURS = '40'

/** The add / edit form. `member` null = a new person. */
export function MemberForm({ member, team, skills, skillsLoading, saving, serverError, onSave, onCancel }: {
  member: ProjectTeamMember | null
  team: ProjectTeamMember[]
  skills: ProjectSkill[]
  skillsLoading: boolean
  saving: boolean
  serverError: unknown
  onSave: (input: TeamMemberInput) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(member?.fullName ?? '')
  const [role, setRole] = useState(member?.roleTitle ?? '')
  const [hours, setHours] = useState(member ? String(member.hoursPerWeek) : NEW_MEMBER_HOURS)
  const [picked, setPicked] = useState<Set<string>>(() => new Set(member?.skills ?? []))
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
    setProblems(found)
    if (found.length > 0) return
    const listed = new Set(skills.map((skill) => skill.name))
    const keptOutsideList = (member?.skills ?? []).filter((skillName) => !listed.has(skillName))
    const chosen = skills.map((skill) => skill.name).filter((skillName) => picked.has(skillName))
    onSave({ fullName, roleTitle, hoursPerWeek, skills: [...chosen, ...keptOutsideList] })
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
      <div className="g3">
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
          <label htmlFor="mf-hours">
            Hours per week on this project<span className="req">*</span>
          </label>
          <input className="in" id="mf-hours" type="number" min={1} max={80} step={1} value={hours} onChange={(event) => setHours(event.target.value)} />
        </div>
      </div>
      <div className="field mt">
        <label id="mf-skills-label">Skills</label>
        <div className="skill-picks" role="group" aria-labelledby="mf-skills-label">
          {skillsLoading ? (
            <span className="small muted">Loading skills&hellip;</span>
          ) : skills.length > 0 ? (
            skills.map((skill) => (
              <label className="pick" key={skill.skillId}>
                <input type="checkbox" value={skill.name} checked={picked.has(skill.name)} onChange={() => togglePick(skill.name)} /> {skill.name}
              </label>
            ))
          ) : (
            <span className="small muted">Add skills below first.</span>
          )}
        </div>
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
