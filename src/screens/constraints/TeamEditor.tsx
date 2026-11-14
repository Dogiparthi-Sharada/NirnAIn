/**
 * The Team & capacity card on step 5 (approved mockup views/constraints-team.js): who works on
 * this project, their role and hourly rate (blank = the optimizer's default rate), the hours per
 * week each can give it, and how much of that other projects already book (utilization), with the
 * capacity Must hold / Can flex switch.
 *
 * Capacity rule: Must hold = nobody is scheduled beyond the hours entered here; Can flex = people
 * may be scheduled up to 25% above them (overtime), at a penalty. Hours are edited inline (whole
 * numbers 1–80; anything else is explained and reverted). Data: `GET/POST/PATCH/DELETE
 * …/team` (contract [B]); every change refreshes the step 6 confirmation sheet.
 */
import { useState } from 'react'
import { UtilBar } from '@/components/InputsKit'
import { errorMessage } from '@/lib/api'
import { policyOf, type PolicyInputs, type PolicyRisk } from '@/lib/constraintPolicy'
import { ADD_MEMBER_ID, hoursText, policyChanges, useSaveFailure, type ConstraintChanges } from '@/lib/constraints'
import {
  hourlyRateText,
  isWholeHours,
  ROLES,
  useAddTeamMember,
  useRemoveTeamMember,
  useTeam,
  useUpdateTeamMember,
  utilization,
  type ProjectTeamMember,
  type TeamMemberInput,
} from '@/lib/inputs'
import { toast } from '@/lib/toast'
import { CommitInput } from './CommitInput'
import { MemberForm } from './MemberForm'
import { PolicyNotes, PolicyToggle } from './PolicyControls'

const HOURS_RULE = 'Hours per week must be a whole number from 1 to 80.'

/** Only the fields the PM changed, so an edit never overwrites anything else. */
function memberChanges(member: ProjectTeamMember, input: TeamMemberInput): Partial<TeamMemberInput> {
  const changes: Partial<TeamMemberInput> = {}
  if (input.fullName !== member.fullName) changes.fullName = input.fullName
  if (input.roleTitle !== (member.roleTitle ?? '')) changes.roleTitle = input.roleTitle
  if (input.hoursPerWeek !== member.hoursPerWeek) changes.hoursPerWeek = input.hoursPerWeek
  if ((input.hourlyRateCents ?? null) !== (member.hourlyRateCents ?? null)) changes.hourlyRateCents = input.hourlyRateCents ?? null
  return changes
}

/** One person: name and role, inline hours, utilization, Edit / Remove. */
function MemberRow({ member, onHours, onEdit, onRemove }: {
  member: ProjectTeamMember
  onHours: (text: string) => void
  onEdit: () => void
  onRemove: () => void
}) {
  const over = member.bookedHours - member.hoursPerWeek
  return (
    <tr>
      <td>
        <b>{member.fullName}</b>
        <div className="small muted">
          {member.roleTitle || 'No role yet'} &middot; {hourlyRateText(member.hourlyRateCents)}
        </div>
      </td>
      <td>
        <CommitInput
          className="in hrs"
          type="number"
          min={1}
          max={80}
          step={1}
          data-hrs={member.employeeId}
          aria-label={`Hours per week for ${member.fullName}`}
          value={String(member.hoursPerWeek)}
          onCommit={onHours}
        />
      </td>
      <td>
        <UtilBar pct={utilization(member)} />
        <div className="small muted">
          {hoursText(member.bookedHours)} of {hoursText(member.hoursPerWeek)} h allocated
        </div>
        {over > 0 && (
          <div className="small" style={{ color: 'var(--red)' }}>
            Over by {hoursText(over)} h/wk
          </div>
        )}
      </td>
      <td className="row-acts">
        <button className="btn sec sm" type="button" data-medit={member.employeeId} onClick={onEdit} aria-label={`Edit ${member.fullName}`}>
          Edit
        </button>{' '}
        <button className="btn sec sm danger" type="button" data-mrm={member.employeeId} onClick={onRemove} aria-label={`Remove ${member.fullName}`}>
          Remove
        </button>
      </td>
    </tr>
  )
}

/** The team card: the people, their hours and utilization, the add / edit form and the capacity switch. */
export function TeamEditor({ projectId, policy, risks, onSavePolicy }: {
  projectId: string
  /** Null while the constraint set is loading or failed: the card works without its switch. */
  policy: PolicyInputs | null
  risks: PolicyRisk[]
  onSavePolicy: (changes: ConstraintChanges) => void
}) {
  const teamQuery = useTeam(projectId)
  const addMember = useAddTeamMember(projectId)
  const updateMember = useUpdateTeamMember(projectId)
  const updateHours = useUpdateTeamMember(projectId)
  const removeMember = useRemoveTeamMember(projectId)
  const reportFailure = useSaveFailure(projectId)
  // 'new' = the add form; an employee id = editing that person; null = closed.
  const [formFor, setFormFor] = useState<string | null>(null)

  const team = teamQuery.data ?? []
  const editing = team.find((member) => member.employeeId === formFor) ?? null
  const formSaving = addMember.isPending || updateMember.isPending
  const formError = formFor === 'new' ? addMember.error : updateMember.error

  function openForm(target: string) {
    addMember.reset()
    updateMember.reset()
    setFormFor(target)
  }

  function saveForm(input: TeamMemberInput) {
    if (!editing) {
      addMember.mutate(input, {
        onSuccess: () => {
          setFormFor(null)
          toast(`${input.fullName} added. The optimizer will assign their work.`)
        },
      })
      return
    }
    const changes = memberChanges(editing, input)
    if (Object.keys(changes).length === 0) {
      setFormFor(null)
      return
    }
    updateMember.mutate(
      { employeeId: editing.employeeId, patch: changes },
      {
        onSuccess: () => {
          setFormFor(null)
          toast(`${input.fullName} updated.`)
        },
      },
    )
  }

  function saveHours(member: ProjectTeamMember, text: string) {
    const hours = Number(text)
    if (text.trim() === '' || !isWholeHours(hours)) {
      toast(HOURS_RULE)
      return
    }
    updateHours.mutate({ employeeId: member.employeeId, patch: { hoursPerWeek: hours } }, { onError: reportFailure })
  }

  function remove(member: ProjectTeamMember) {
    if (formFor === member.employeeId) setFormFor(null)
    removeMember.mutate(member.employeeId, {
      onSuccess: () => toast(`${member.fullName} removed from this project.`),
      onError: reportFailure,
    })
  }

  const totalHours = team.reduce((sum, member) => sum + member.hoursPerWeek, 0)
  const overCount = team.filter((member) => member.bookedHours > member.hoursPerWeek).length

  let body
  if (teamQuery.isPending) {
    body = (
      <tr>
        <td colSpan={4} className="small muted" role="status">
          Loading the team&hellip;
        </td>
      </tr>
    )
  } else if (teamQuery.isError) {
    body = (
      <tr>
        <td colSpan={4} className="small" role="alert">
          Couldn&rsquo;t load the team. {errorMessage(teamQuery.error)}{' '}
          <button className="btn sec sm" type="button" onClick={() => void teamQuery.refetch()}>
            Try again
          </button>
        </td>
      </tr>
    )
  } else if (team.length === 0) {
    body = (
      <tr>
        <td colSpan={4} className="small muted">
          No one on the team yet. Add the people who will work on this project.
        </td>
      </tr>
    )
  } else {
    body = team.map((member) => (
      <MemberRow
        key={member.employeeId}
        member={member}
        onHours={(text) => saveHours(member, text)}
        onEdit={() => openForm(member.employeeId)}
        onRemove={() => remove(member)}
      />
    ))
  }

  return (
    <div className="card span2" id="con-team">
      <div className="card-head">
        <h3>Team &amp; capacity</h3>
        {policy && <PolicyToggle policyKey="capacity" mode={policyOf('capacity', policy)} onChange={(mode) => onSavePolicy(policyChanges('capacity', mode))} />}
      </div>
      <p className="small muted" style={{ margin: '0 0 8px' }}>
        Who works on this project, their role, and how many hours a week they can give it. <b>Allocated</b> is what other projects already
        book of those hours in this plan&rsquo;s window; the optimizer plans around it.
      </p>
      {policy && <PolicyNotes policyKey="capacity" policy={policy} risks={risks} />}
      <div className="tbl-scroll mt">
        <table className="tbl">
          <thead>
            <tr>
              <th>Person</th>
              <th>Hours / week</th>
              <th>Utilization</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody id="team-body">{body}</tbody>
        </table>
      </div>
      {formFor !== null && (formFor === 'new' || editing) && (
        <MemberForm
          key={formFor}
          member={editing}
          team={team}
          saving={formSaving}
          serverError={formError}
          onSave={saveForm}
          onCancel={() => setFormFor(null)}
        />
      )}
      <div className="row between mt">
        <span className="small muted" id="team-sum">
          {teamQuery.isSuccess && (
            <>
              {team.length} {team.length === 1 ? 'person' : 'people'} &middot; {hoursText(totalHours)} h/wk available
              {overCount > 0 && (
                <>
                  {' '}
                  &middot; <b style={{ color: 'var(--red)' }}>{overCount} over capacity</b>
                </>
              )}
            </>
          )}
        </span>
        <button className="btn sec sm" id={ADD_MEMBER_ID} type="button" onClick={() => openForm('new')}>
          + Add team member
        </button>
      </div>
      <datalist id="role-list">
        {ROLES.map((role) => (
          <option key={role} value={role} />
        ))}
      </datalist>
    </div>
  )
}
