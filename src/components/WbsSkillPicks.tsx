/**
 * Work-package skills on the WBS Builder: the chips a row shows and the picker of its edit row.
 *
 * Purpose: The optimizer gives a Required skill's work only to people who have it (or to an
 * allowed contractor) and finds that work through these tags. A chip is red when nobody on the
 * team has the skill, green when someone does and grey when it isn't on the project's list; skills
 * outside the list stay pickable so they can be kept or removed.
 * How it fits: Used by `components/WbsTable.tsx` and `components/WbsEditCells.tsx`; picks from the
 * project's skill list (step 5) via `lib/wbs.ts`; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import type { KeyboardEvent } from 'react'
import { Link } from 'react-router'
import type { ProjectSkill } from '@/lib/inputs'
import { stepPath } from '@/lib/steps'
import type { WbsSkill } from '@/lib/types'
import { skillNameKey, type SkillOptions } from '@/lib/wbs'

/** A package's skills as chips: red = nobody on the team has it, green = covered, grey = off the list. */
export function WbsSkillChips({ skills, projectSkills }: { skills: WbsSkill[]; projectSkills: ProjectSkill[] | undefined }) {
  if (skills.length === 0) return null
  const byName = new Map((projectSkills ?? []).map((skill) => [skillNameKey(skill.name), skill]))
  return (
    <div className="row" style={{ gap: 4, marginTop: 4 }}>
      <span className="sr-only">Skills needed:</span>
      {skills.map((skill) => {
        const listed = byName.get(skillNameKey(skill.name))
        const gap = listed !== undefined && listed.holders.length === 0
        const kind = listed === undefined ? 'st-default' : gap ? 'st-missing' : 'st-stated'
        const why = listed === undefined ? 'Not on the project\u2019s skill list' : gap ? 'Nobody on the team has it' : `Who has it: ${listed.holders.join(', ')}`
        return (
          <span className={`chip ${kind}`} key={skill.skillId} title={why}>
            {gap && '\u2717 '}
            {skill.name}
            {gap && <span className="sr-only"> (nobody on the team has it)</span>}
          </span>
        )
      })}
    </div>
  )
}

/** The edit row's skill picker, with its loading, error, empty and not-supported states. */
export function WbsSkillPicks({ nodeId, options, choices, isPicked, onToggle, onKeyDown }: {
  nodeId: string
  options: SkillOptions
  choices: string[]
  isPicked: (name: string) => boolean
  onToggle: (name: string) => void
  onKeyDown: (event: KeyboardEvent) => void
}) {
  const labelId = `wbs-skills-${nodeId}`
  const constraintsLink = <Link to={stepPath('constraints', options.projectId)}>step 5, Constraints</Link>
  let body
  if (options.unsupported) {
    body = <span className="small muted">This server can&rsquo;t save skills on work packages yet, so tagging is off. Name, weeks and owner still save.</span>
  } else if (options.loading) {
    body = (
      <span className="small muted" role="status">
        Loading the project&rsquo;s skills&hellip;
      </span>
    )
  } else if (options.error !== null) {
    body = (
      <span className="small" role="alert">
        Couldn&rsquo;t load the skill list. {options.error}{' '}
        <button className="btn sec sm" type="button" onClick={options.retry}>
          Try again
        </button>
      </span>
    )
  } else if (choices.length === 0) {
    body = <span className="small muted">No skills on the project&rsquo;s list yet. Add them on {constraintsLink}.</span>
  } else {
    body = choices.map((name) => (
      <label className="pick" key={name}>
        <input type="checkbox" value={name} checked={isPicked(name)} onChange={() => onToggle(name)} onKeyDown={onKeyDown} /> {name}
      </label>
    ))
  }
  return (
    <div className="mt" onClick={(event) => event.stopPropagation()}>
      <span className="small muted" id={labelId}>
        Skills needed
      </span>
      <div className="skill-picks" role="group" aria-labelledby={labelId} style={{ marginTop: 4 }}>
        {body}
      </div>
    </div>
  )
}

/** Shown once the server refused the skills of a save: why tagging is off and what was kept. */
export function WbsSkillsOffBanner() {
  return (
    <div className="banner" id="wbs-skills-off" role="status">
      <b>Skills weren&rsquo;t saved.</b> This server doesn&rsquo;t accept skills on work packages yet, so skill tagging is off until it is
      updated. Your other changes were saved.
    </div>
  )
}
