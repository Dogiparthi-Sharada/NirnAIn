/**
 * The Required skills card on step 5: the skills the project needs, who has each one and the gaps.
 *
 * Purpose: Must hold sends work only to people who have the skill (or an allowed specialist); Can
 * flex may give it to someone without it, with ramp-up time. A required skill nobody has is a gap:
 * it makes the specialist question count and is called out below the table.
 * How it fits: Port of the mockup's views/constraints-team.js `renderSkills`; rendered by
 * `screens/Constraints.tsx`; uses the skill hooks in `lib/inputs.ts` (contract [B]; names unique,
 * case-insensitive); layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { Fragment, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { policyOf, type PolicyInputs, type PolicyRisk } from '@/lib/constraintPolicy'
import { policyChanges, useSaveFailure, type ConstraintChanges } from '@/lib/constraints'
import { skillGaps, useAddSkill, useProjectSkills, useRemoveSkill, useUpdateSkill, type ProjectSkill } from '@/lib/inputs'
import { toast } from '@/lib/toast'
import { CommitInput } from './CommitInput'
import { PolicyNotes, PolicyToggle } from './PolicyControls'

const NEEDS = [
  { label: 'Required', required: true },
  { label: 'Optional', required: false },
]

/** Trimmed, with inner runs of spaces collapsed (the server stores names the same way). */
function cleanName(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(' ')
}

/** One skill: inline name, Required / Optional, who has it, Remove. */
function SkillRow({ skill, onRename, onNeed, onRemove }: {
  skill: ProjectSkill
  onRename: (text: string) => void
  onNeed: (required: boolean) => void
  onRemove: () => void
}) {
  return (
    <tr>
      <td>
        <CommitInput className="in sm" data-skname={skill.skillId} maxLength={60} aria-label="Skill name" value={skill.name} onCommit={onRename} />
      </td>
      <td>
        <span className="seg sm" role="group" aria-label={`${skill.name}: needed`}>
          {NEEDS.map((need) => (
            <button
              key={need.label}
              type="button"
              data-skneed={skill.skillId}
              data-v={need.label}
              className={skill.required === need.required ? 'on' : undefined}
              aria-pressed={skill.required === need.required}
              onClick={() => onNeed(need.required)}
            >
              {need.label}
            </button>
          ))}
        </span>
      </td>
      <td>
        {skill.holders.length > 0 ? (
          skill.holders.map((holder, position) => (
            <Fragment key={holder}>
              {position > 0 && ' '}
              <span className="chip st-stated">{holder}</span>
            </Fragment>
          ))
        ) : (
          <span className={`chip ${skill.required ? 'st-missing' : 'st-default'}`}>{skill.required ? '\u2717 Nobody yet' : 'Nobody yet'}</span>
        )}
      </td>
      <td className="row-acts">
        <button className="btn sec sm danger" type="button" data-skrm={skill.skillId} onClick={onRemove} aria-label={`Remove ${skill.name}`}>
          Remove
        </button>
      </td>
    </tr>
  )
}

/** The skills card: the project's skills, who has each one, the gaps and the skills Must hold / Can flex switch. */
export function SkillsEditor({ projectId, policy, risks, onSavePolicy }: {
  projectId: string
  /** Null while the constraint set is loading or failed: the card works without its switch. */
  policy: PolicyInputs | null
  risks: PolicyRisk[]
  onSavePolicy: (changes: ConstraintChanges) => void
}) {
  const skillsQuery = useProjectSkills(projectId)
  const addSkill = useAddSkill(projectId)
  const updateSkill = useUpdateSkill(projectId)
  const removeSkill = useRemoveSkill(projectId)
  const reportFailure = useSaveFailure(projectId)
  const [newName, setNewName] = useState('')
  const [newRequired, setNewRequired] = useState(true)
  const [addError, setAddError] = useState('')

  const skills = skillsQuery.data ?? []
  const gaps = skillGaps(skills)

  /** An error message for a name, or '' when it can be saved. `skipId` = the skill being renamed. */
  function nameError(name: string, skipId: string | null): string {
    if (!name) return 'Enter a skill name.'
    if (skills.some((skill) => skill.skillId !== skipId && skill.name.toLowerCase() === name.toLowerCase())) return `${name} is already in the list.`
    return ''
  }

  /** Adds the typed skill (Required or Optional) unless the name is empty or already listed. */
  function add() {
    const name = cleanName(newName)
    const problem = nameError(name, null)
    setAddError(problem)
    if (problem) return
    addSkill.mutate(
      { name, required: newRequired },
      {
        onSuccess: () => setNewName(''),
        onError: (error) => setAddError(errorMessage(error)),
      },
    )
  }

  /** Renames a skill; a clash or empty name is explained in a toast. */
  function rename(skill: ProjectSkill, text: string) {
    const name = cleanName(text)
    const problem = nameError(name, skill.skillId)
    if (problem) toast(problem)
    else if (name !== skill.name) updateSkill.mutate({ skillId: skill.skillId, patch: { name } }, { onError: reportFailure })
  }

  /** Switches a skill between Required and Optional. */
  function setNeed(skill: ProjectSkill, required: boolean) {
    if (skill.required === required) return
    updateSkill.mutate({ skillId: skill.skillId, patch: { required } }, { onError: reportFailure })
  }

  /** Removes a skill from the project's list. */
  function remove(skill: ProjectSkill) {
    removeSkill.mutate(skill.skillId, { onSuccess: () => toast(`${skill.name} removed.`), onError: reportFailure })
  }

  let body
  if (skillsQuery.isPending) {
    body = (
      <tr>
        <td colSpan={4} className="small muted" role="status">
          Loading the skills&hellip;
        </td>
      </tr>
    )
  } else if (skillsQuery.isError) {
    body = (
      <tr>
        <td colSpan={4} className="small" role="alert">
          Couldn&rsquo;t load the skills. {errorMessage(skillsQuery.error)}{' '}
          <button className="btn sec sm" type="button" onClick={() => void skillsQuery.refetch()}>
            Try again
          </button>
        </td>
      </tr>
    )
  } else if (skills.length === 0) {
    body = (
      <tr>
        <td colSpan={4} className="small muted">
          No skills listed.
        </td>
      </tr>
    )
  } else {
    body = skills.map((skill) => (
      <SkillRow
        key={skill.skillId}
        skill={skill}
        onRename={(text) => rename(skill, text)}
        onNeed={(required) => setNeed(skill, required)}
        onRemove={() => remove(skill)}
      />
    ))
  }

  return (
    <div className="card span2" id="con-skills">
      <div className="card-head">
        <h3>Required skills</h3>
        {policy && <PolicyToggle policyKey="skills" mode={policyOf('skills', policy)} onChange={(mode) => onSavePolicy(policyChanges('skills', mode))} />}
      </div>
      {policy && <PolicyNotes policyKey="skills" policy={policy} risks={risks} />}
      <div className="tbl-scroll mt">
        <table className="tbl">
          <thead>
            <tr>
              <th>Skill</th>
              <th>Needed</th>
              <th>Who has it</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody id="skill-body">{body}</tbody>
        </table>
      </div>
      <div className="row mt">
        <input
          className="in sm"
          id="new-skill"
          placeholder="Add a skill, e.g. SAP Integration"
          maxLength={60}
          aria-label="New skill name"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') add()
          }}
        />
        <select
          className="in sm"
          id="new-skill-need"
          aria-label="Needed"
          value={newRequired ? 'Required' : 'Optional'}
          onChange={(event) => setNewRequired(event.target.value === 'Required')}
        >
          <option>Required</option>
          <option>Optional</option>
        </select>
        <button className="btn sec sm" id="btn-add-skill" type="button" onClick={add} disabled={addSkill.isPending} aria-busy={addSkill.isPending || undefined}>
          {addSkill.isPending ? 'Adding\u2026' : '+ Add skill'}
        </button>
      </div>
      <p className="err" id="skill-err" role="alert" hidden={!addError}>
        {addError}
      </p>
      {skillsQuery.isSuccess && (
        <div className="warnbox" id="skill-gaps" style={{ background: gaps.length > 0 ? '#F3D9D5' : '#EAF0E2' }}>
          {gaps.length > 0 ? (
            <>
              &#9888; Nobody on the team has{' '}
              {gaps.map((gap, position) => (
                <Fragment key={gap}>
                  {position > 0 && ', '}
                  <b>{gap}</b>
                </Fragment>
              ))}
              . Add someone who has it, allow an outside specialist (question above), or mark it Optional.
            </>
          ) : (
            '\u2713 Every required skill is covered by someone on the team.'
          )}
        </div>
      )}
    </div>
  )
}
