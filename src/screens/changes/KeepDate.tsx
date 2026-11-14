/**
 * "Ways to keep the date" on step 7 (round 2, D2): after a re-plan that finishes later than
 * the plan in force, or finds no plan at all, the PM sees how far it slipped and the priced
 * ways to keep the date, and decides. Nothing is chosen automatically.
 *
 * Data: the re-plan run's `result.summary` (completionDate, targetDate, slipDays) and
 * `result.remedies[]` (`keepDateOf` in `src/lib/changes.ts`). "Use this option" re-plans the
 * change with that `remedy` (plus its overrides, change id and baseline); "Accept {date}"
 * approves the slipped plan as it is, or, when no plan was found, re-plans with the new date.
 * Markup: the mockup's advice card ("Your call") from the Optimization Results screen. An
 * infeasible re-plan's wording follows its conflict (budget, date, skills or hours, QA NEW-7h)
 * and "Change these together" (R6 `combined`) lists every change it makes; overtime always
 * shows its paid hours (helpers shared with step 6 in `optimization/KeepDateCard.tsx`).
 */
import type { KeepDate, KeepDateRemedyId } from '@/lib/changes'
import { formatDate } from '@/lib/format'
import { blockerWording, changeCount, optionCost, RemedyChanges } from '../optimization/KeepDateCard'
import { Chip } from './ui'

/** The card, or nothing when the re-plan keeps the date. `infeasible` = the re-plan found no plan. */
export function KeepDateCard({
  keepDate,
  infeasible,
  conflicts,
  busy,
  onUse,
  onAccept,
}: {
  keepDate: KeepDate
  infeasible: boolean
  /** The infeasible re-plan's conflicts (`run.iis`): they decide the wording. */
  conflicts: { name: string }[]
  busy: boolean
  onUse: (remedy: KeepDateRemedyId) => void
  onAccept: () => void
}) {
  const { completionDate, targetDate, slipDays, remedies } = keepDate
  if (!infeasible && slipDays <= 0) return null
  const target = formatDate(targetDate)
  const newDate = remedies.find((remedy) => remedy.id === 'move_deadline')
  // Accepting the new date: the slipped plan itself, or (no plan found) the move-deadline re-plan.
  const acceptDate = infeasible ? (newDate?.completionDate ?? null) : completionDate
  const options = remedies.filter((remedy) => remedy.id !== 'move_deadline')
  const canAccept = !infeasible || newDate !== undefined
  const wording = infeasible ? blockerWording(conflicts, targetDate) : null
  let title = targetDate ? `Ways to keep ${target}` : 'Ways to keep the date'
  if (wording) title = wording.title

  return (
    <div className="card advice" id="keep-date">
      <div className="adv-top">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <Chip kind="default">Your call</Chip>
      </div>
      <p className="small" style={{ margin: '6px 0 0' }}>
        {wording ? (
          <>{wording.problem} Ways to get a plan:</>
        ) : (
          <>
            This plan finishes <b>{formatDate(completionDate)}</b>, {slipDays} working day{slipDays === 1 ? '' : 's'} after {target}. Ways to keep {target}:
          </>
        )}
      </p>
      {options.length === 0 && <p className="small muted">{wording ? wording.none : 'No option reaches an earlier date with these changes.'}</p>}
      {options.map((remedy) => (
        <div className="adv decide" key={remedy.id} data-remedy={remedy.id}>
          <div className="row between">
            <span>
              <b>{remedy.title}</b> {remedy.changes?.length ? <Chip kind="inferred">{changeCount(remedy)}</Chip> : null}
            </span>
            <button
              className="btn sm sec"
              type="button"
              disabled={busy}
              aria-label={`Use this option: ${remedy.title}`}
              onClick={() => onUse(remedy.id)}
            >
              Use this option
            </button>
          </div>
          <div className="adv-why">{remedy.detail}</div>
          <RemedyChanges option={remedy} />
          <div className="adv-fix">
            Finishes {formatDate(remedy.completionDate)} &middot; {optionCost(remedy)}
          </div>
        </div>
      ))}
      {canAccept && (
        <div className="row end mt8">
          <button className="btn" type="button" id="btn-accept-date" disabled={busy} onClick={infeasible ? () => onUse('move_deadline') : onAccept}>
            Accept {formatDate(acceptDate)}
          </button>
        </div>
      )}
    </div>
  )
}
