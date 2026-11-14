/**
 * "Ways to keep the date" on step 7 (round 2, D2): after a re-plan that finishes later than
 * the plan in force, or finds no plan at all, the PM sees how far it slipped and the priced
 * ways to keep the date, and decides. Nothing is chosen automatically.
 *
 * Data: the re-plan run's `result.summary` (completionDate, targetDate, slipDays) and
 * `result.remedies[]` (`keepDateOf` in `src/lib/changes.ts`). "Use this option" re-plans the
 * change with that `remedy` (plus its overrides, change id and baseline); "Accept {date}"
 * approves the slipped plan as it is, or, when no plan was found, re-plans with the new date.
 * Markup: the mockup's advice card ("Your call") from the Optimization Results screen.
 */
import type { KeepDate, KeepDateRemedy, KeepDateRemedyId } from '@/lib/changes'
import { formatDate, formatMoney } from '@/lib/format'
import { Chip } from './ui'

function extraCost(remedy: KeepDateRemedy): string {
  if (remedy.costCents === null) return 'cost not priced'
  return remedy.costCents > 0 ? `+${formatMoney(remedy.costCents)}` : 'no extra cost'
}

/** The card, or nothing when the re-plan keeps the date. `infeasible` = the re-plan found no plan. */
export function KeepDateCard({
  keepDate,
  infeasible,
  busy,
  onUse,
  onAccept,
}: {
  keepDate: KeepDate
  infeasible: boolean
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

  return (
    <div className="card advice" id="keep-date">
      <div className="adv-top">
        <h3 style={{ margin: 0 }}>{targetDate ? `Ways to keep ${target}` : 'Ways to keep the date'}</h3>
        <Chip kind="default">Your call</Chip>
      </div>
      <p className="small" style={{ margin: '6px 0 0' }}>
        {infeasible ? (
          <>No plan meets every hard constraint with these changes. Ways to get a plan:</>
        ) : (
          <>
            This plan finishes <b>{formatDate(completionDate)}</b>, {slipDays} working day{slipDays === 1 ? '' : 's'} after {target}. Ways to keep {target}:
          </>
        )}
      </p>
      {options.length === 0 && <p className="small muted">No option reaches an earlier date with these changes.</p>}
      {options.map((remedy) => (
        <div className="adv decide" key={remedy.id}>
          <div className="row between">
            <b>{remedy.title}</b>
            <button className="btn sm sec" type="button" disabled={busy} onClick={() => onUse(remedy.id)}>
              Use this option
            </button>
          </div>
          <div className="adv-why">{remedy.detail}</div>
          <div className="adv-fix">
            Finishes {formatDate(remedy.completionDate)} &middot; {extraCost(remedy)}
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
