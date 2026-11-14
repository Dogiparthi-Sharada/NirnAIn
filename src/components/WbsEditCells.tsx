/**
 * The inline editor of one WBS row: Name, Duration, Owner, skills and Save / Cancel cells.
 *
 * Purpose: The editor checks what the PM typed before anything is saved and says why inline
 * instead of silently dropping or changing a value (QA W-07, W-08): a name can't be blank, weeks
 * must be a plain number of zero or more within the server's limit, and a fraction shows its
 * rounding to working days before saving. Enter saves, Escape cancels; skills are sent only when
 * changed.
 * How it fits: Rendered by `components/WbsTable.tsx` in place of a row's read-only cells; uses the
 * rules in `lib/wbs.ts` and the picker in `components/WbsSkillPicks.tsx`; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useRef, useState, type KeyboardEvent } from 'react'
import { DAYS_PER_WEEK, parseWeeks, skillChoices, useSkillPicks, type SkillOptions, type WbsTreeNode } from '@/lib/wbs'
import type { WbsNodePatch } from '@/lib/types'
import { WbsSkillPicks } from './WbsSkillPicks'

interface Problems {
  name?: string
  weeks?: string
}

/** The Name, Duration, Owner and action cells of a row being edited; `onSave` gets only valid values. */
export default function WbsEditCells({ node, owners, skillOptions, onSave, onCancel }: {
  node: WbsTreeNode
  owners: { id: string; name: string }[]
  skillOptions: SkillOptions
  onSave: (patch: WbsNodePatch) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(node.name)
  const [weeks, setWeeks] = useState(node.durationDays == null ? '' : String(node.durationDays / DAYS_PER_WEEK))
  const [ownerId, setOwnerId] = useState(node.ownerEmployeeId ?? '')
  const [problems, setProblems] = useState<Problems>({})
  const skillPicks = useSkillPicks(node.skills)
  const nameRef = useRef<HTMLInputElement>(null)
  const weeksRef = useRef<HTMLInputElement>(null)

  const workPackage = node.level === 3
  const typedWeeks = parseWeeks(weeks)
  const nameErrorId = `wbs-name-err-${node.id}`
  const choices = skillChoices(skillOptions.skills, node.skills)
  const weeksNoteId = `wbs-weeks-note-${node.id}`

  /**
   * Checks the typed values, focuses the first problem, else saves the changes (skills only when changed).
   */
  function save() {
    const trimmedName = name.trim()
    const found: Problems = {}
    if (!trimmedName) found.name = 'Give it a name.'
    if (workPackage && typedWeeks.problem) found.weeks = typedWeeks.problem
    setProblems(found)
    if (found.name) {
      nameRef.current?.focus()
      return
    }
    if (found.weeks) {
      weeksRef.current?.focus()
      return
    }
    const patch: WbsNodePatch = { name: trimmedName }
    if (workPackage) {
      patch.durationDays = typedWeeks.days
      patch.ownerEmployeeId = ownerId || null
      const skills = skillOptions.unsupported || skillOptions.loading ? null : skillPicks.changedNames(choices)
      if (skills) patch.skills = skills
    }
    onSave(patch)
  }

  /** Enter saves and Escape cancels; the keys don't reach the table's own shortcuts. */
  function editKeys(event: KeyboardEvent) {
    event.stopPropagation()
    if (event.key === 'Enter') save()
    if (event.key === 'Escape') onCancel()
  }

  // Under the weeks: why the last save was refused, else how a fraction rounds to working days.
  const weeksNote = problems.weeks ?? (typedWeeks.rounded && typedWeeks.days !== null ? `${weeks.trim()} w = ${typedWeeks.days} day${typedWeeks.days === 1 ? '' : 's'}` : null)

  return (
    <>
      <td>
        <input
          className={problems.name ? 'in sm invalid' : 'in sm'}
          style={{ width: '100%' }}
          ref={nameRef}
          value={name}
          onChange={(event) => {
            setName(event.target.value)
            setProblems((previous) => ({ ...previous, name: undefined }))
          }}
          onKeyDown={editKeys}
          onClick={(event) => event.stopPropagation()}
          aria-label="Name"
          aria-invalid={problems.name ? true : undefined}
          aria-describedby={problems.name ? nameErrorId : undefined}
          maxLength={300}
          autoFocus
        />
        {problems.name && (
          <p className="err" id={nameErrorId} role="alert">
            {problems.name}
          </p>
        )}
        {workPackage && (
          <WbsSkillPicks
            nodeId={node.id}
            options={skillOptions}
            choices={choices}
            isPicked={skillPicks.isPicked}
            onToggle={skillPicks.toggle}
            onKeyDown={editKeys}
          />
        )}
      </td>
      <td>
        {workPackage && (
          <>
            <input
              className={problems.weeks ? 'in sm invalid' : 'in sm'}
              style={{ width: 80 }}
              ref={weeksRef}
              value={weeks}
              onChange={(event) => {
                setWeeks(event.target.value)
                setProblems((previous) => ({ ...previous, weeks: undefined }))
              }}
              onKeyDown={editKeys}
              onClick={(event) => event.stopPropagation()}
              aria-label="Duration in weeks"
              aria-invalid={problems.weeks ? true : undefined}
              aria-describedby={weeksNote ? weeksNoteId : undefined}
              inputMode="decimal"
              placeholder="weeks"
            />
            {weeksNote && (
              <p className={problems.weeks ? 'err' : 'note'} id={weeksNoteId} role={problems.weeks ? 'alert' : undefined}>
                {weeksNote}
              </p>
            )}
          </>
        )}
      </td>
      <td className="small">
        {workPackage && (
          <select
            className="in sm"
            style={{ width: '100%' }}
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            aria-label="Owner"
          >
            <option value="">Unassigned</option>
            {owners.map((owner) => (
              <option key={owner.id} value={owner.id}>
                {owner.name}
              </option>
            ))}
          </select>
        )}
      </td>
      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
        <button
          className="btn sm"
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            save()
          }}
        >
          Save
        </button>{' '}
        <button
          className="btn sec sm"
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onCancel()
          }}
        >
          Cancel
        </button>
      </td>
    </>
  )
}
