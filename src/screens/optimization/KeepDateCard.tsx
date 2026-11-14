/**
 * "Ways to keep the date" on Optimization Results: shown on a plan that finishes after the date it
 * tries to keep (contract D2). It says by how much, lists each priced, re-solved way to keep the
 * date (bring in a contractor, overtime up to 25%, …) with its finish date and extra cost, and
 * offers to accept the later date instead.
 *
 * Business rule: the project manager decides; nothing is chosen automatically. "Use this option"
 * starts a new run with that remedy; "Accept" applies this plan as it is. Styled as the approved
 * mockup's advice card ("Your call", views/optimize-advice.js).
 */
import { formatDate, formatMoney } from '@/lib/format'
import type { DateSlip, RemedyId } from '@/lib/optimize'
import { ProblemAlert } from './ui'

/** What "Accept" can do right now: apply, wait for something first (and why), or already done. */
export type AcceptState =
  | { kind: 'ready'; applying: boolean; onAccept: () => void }
  | { kind: 'blocked'; reason: string }
  | { kind: 'applied'; version: number }

const REASON_ID = 'keep-date-accept-why'
const STARTING_ID = 'keep-date-starting'

/** "+$12,000 extra"; a remedy the server could not price says so. */
function extraCost(costCents: number | null): string {
  return costCents == null ? 'cost not priced' : `+${formatMoney(costCents)} extra`
}

/** The card; `onUse` re-runs the solver with the picked remedy. */
export function KeepDateCard({ slip, starting, startError, onUse, accept }: {
  slip: DateSlip
  starting: boolean
  startError: unknown
  onUse: (remedy: RemedyId) => void
  accept: AcceptState
}) {
  const target = formatDate(slip.targetDate)
  const finish = formatDate(slip.completionDate)
  const days = `${slip.slipDays} working day${slip.slipDays === 1 ? '' : 's'}`

  return (
    <div className="card advice" id="opt-keep-date" role="region" aria-labelledby="keep-date-title">
      <div className="adv-top">
        <h3 style={{ margin: 0 }} id="keep-date-title">
          Go-live after {target}
        </h3>
        <span className="chip st-default">Your call</span>
      </div>
      <p className="small" style={{ margin: '6px 0 0' }}>
        This plan finishes <b>{finish}</b>, {days} after {target}.{' '}
        {slip.waysToKeep.length > 0 ? `Ways to keep ${target}:` : `None of the changes we tried finishes by ${target}.`}
      </p>
      {starting && (
        <p className="small muted" id={STARTING_ID} role="status" style={{ margin: '6px 0 0' }}>
          Starting a run with your choice&hellip;
        </p>
      )}

      {slip.waysToKeep.map((remedy) => (
        <div className="adv decide" key={remedy.id} data-remedy={remedy.id}>
          <div className="row between">
            <b>{remedy.title}</b>
            <button
              className="btn sec sm"
              type="button"
              onClick={() => onUse(remedy.id)}
              disabled={starting}
              aria-describedby={starting ? STARTING_ID : undefined}
              aria-label={`Use this option: ${remedy.title}`}
            >
              Use this option
            </button>
          </div>
          {remedy.detail && <div className="adv-why">{remedy.detail}</div>}
          <div className="adv-fix">
            Finishes <b>{remedy.completionDate ? formatDate(remedy.completionDate) : 'date not reported'}</b> &middot;{' '}
            <b>{extraCost(remedy.costCents)}</b>
          </div>
        </div>
      ))}

      <div className="adv decide" data-remedy="accept">
        <div className="row between">
          <b>Accept {finish}</b>
          {accept.kind === 'applied' ? (
            <span className="chip st-confirmed">{'\u2713'} Applied as Plan v{accept.version}</span>
          ) : accept.kind === 'blocked' ? (
            <button className="btn sm" type="button" disabled aria-describedby={REASON_ID}>
              Accept {finish}
            </button>
          ) : (
            <button className="btn sm" type="button" onClick={accept.onAccept} disabled={accept.applying} aria-busy={accept.applying || undefined}>
              {accept.applying ? 'Applying\u2026' : `Accept ${finish}`}
            </button>
          )}
        </div>
        <div className="adv-fix">
          Keep this plan as it is: go-live <b>{finish}</b>, no extra cost.
          {accept.kind === 'blocked' && (
            <>
              {' '}
              <span id={REASON_ID}>{accept.reason}</span>
            </>
          )}
        </div>
      </div>

      {startError != null && (
        <div className="mt8">
          <ProblemAlert title="The solver didn't start" error={startError} />
        </div>
      )}
    </div>
  )
}
