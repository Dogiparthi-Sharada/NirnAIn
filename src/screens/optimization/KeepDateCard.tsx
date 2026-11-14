/**
 * "Ways to keep the date" on Optimization Results: shown on a plan that finishes after the date it
 * tries to keep (contract D2). It says by how much, lists each priced, re-solved way to keep the
 * date (bring in a contractor, overtime up to 25%, …) with its finish date and extra cost, and
 * offers to accept the later date instead.
 *
 * Business rule: the project manager decides; nothing is chosen automatically. "Use this option"
 * starts a new run with that remedy; "Accept" applies this plan as it is. Styled as the approved
 * mockup's advice card ("Your call", views/optimize-advice.js).
 *
 * Also shared with the infeasible states of step 6 (`RunStates.tsx`) and step 7
 * (`changes/KeepDate.tsx`): wording that follows the kind of limit the plan runs into (budget,
 * date, skills or hours, read from the solver's conflicts) and the round-3 "Change these
 * together" option (R6 `combined`), which lists every change it makes.
 */
import { formatDate, formatMoney } from '@/lib/format'
import type { DateSlip, RemedyId } from '@/lib/optimize'
import { ProblemAlert } from './ui'

/** The kind of limit an infeasible plan runs into. */
export type BlockerKind = 'budget' | 'date' | 'skills' | 'capacity'

// Conflict names the solver reports ("Budget cap: $10,000", "Skill: 1.1.2 … requires …").
const BLOCKER_PATTERNS: [BlockerKind, RegExp][] = [
  ['budget', /^(budget|labor) cap\b/i],
  ['date', /^(deadline|milestone|dependency)\b/i],
  ['skills', /^skill\b/i],
  ['capacity', /^capacity\b/i],
]
const BLOCKER_NAMES: Record<BlockerKind, string> = {
  budget: 'the budget',
  date: 'the deadline',
  skills: 'the required skills',
  capacity: 'people\u2019s hours',
}

/** Which kinds of limit the conflicts name, in the order the solver lists them. */
export function blockerKinds(conflicts: { name: string }[]): BlockerKind[] {
  const kinds: BlockerKind[] = []
  for (const conflict of conflicts) {
    const kind = BLOCKER_PATTERNS.find(([, pattern]) => pattern.test(conflict.name))?.[0]
    if (kind && !kinds.includes(kind)) kinds.push(kind)
  }
  return kinds
}

/** Heading, what blocks the plan, and the sentence for when no option helps. */
export interface BlockerWording {
  title: string
  problem: string
  none: string
}

const capitalized = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/**
 * Wording that follows the conflict: a budget conflict talks about the budget, not the date
 * (QA NEW-7h). A dependency chain alone counts as a date conflict; capacity details only lead
 * when nothing else is named.
 */
export function blockerWording(conflicts: { name: string }[], target: string | null): BlockerWording {
  const named = blockerKinds(conflicts)
  const kinds = named.length > 1 ? named.filter((kind) => kind !== 'capacity') : named
  if (kinds.length > 1) {
    const list = kinds.map((kind) => BLOCKER_NAMES[kind])
    const together = kinds.length === 2 ? 'can\u2019t both hold' : 'can\u2019t all hold together'
    return {
      title: 'Ways to get a plan',
      problem: `${capitalized(list.slice(0, -1).join(', '))} and ${list[list.length - 1]} ${together}.`,
      none: 'No single option gets a plan.',
    }
  }
  switch (kinds[0]) {
    case 'budget':
      return {
        title: 'Over the budget',
        problem: 'No plan fits the budget: the work costs more than it allows.',
        none: 'None of the options we tried fits the budget.',
      }
    case 'date':
      return {
        title: target ? `Ways to keep ${formatDate(target)}` : 'Ways to keep the date',
        problem: 'No plan finishes by the deadline.',
        none: 'No option finishes by the deadline.',
      }
    case 'skills':
      return {
        title: 'Missing skills',
        problem: 'Nobody available has a skill this work needs.',
        none: 'No option covers the missing skill.',
      }
    case 'capacity':
      return {
        title: 'Not enough hours',
        problem: 'The team doesn\u2019t have the hours this work needs.',
        none: 'No option finds the hours.',
      }
    default:
      return {
        title: 'Ways to get a plan',
        problem: 'No plan meets every hard constraint.',
        none: 'None of the options we tried gets a plan.',
      }
  }
}

/** One thing the "Change these together" option changes (contract R6). */
export interface RemedyChange {
  kind: 'deadline' | 'budget' | 'specialist' | 'capacity'
  label: string
}

/** The fields of a priced option this file reads; `changes` only on the combined option. */
export interface PricedOption {
  id: string
  detail: string
  costCents: number | null
  changes?: RemedyChange[]
}

/** "1 change" / "3 changes": how many inputs an option changes. */
export function changeCount(option: PricedOption): string {
  const count = option.changes?.length ?? 1
  return `${count} change${count === 1 ? '' : 's'}`
}

/** The changes of the combined option, one per line (nothing for a single option). */
export function RemedyChanges({ option }: { option: PricedOption }) {
  if (!option.changes?.length) return null
  return (
    <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>
      {option.changes.map((change) => (
        <li key={`${change.kind}-${change.label}`}>{change.label}</li>
      ))}
    </ul>
  )
}

// "134.8 overtime hours (1.5x for employees)" in the overtime option's detail.
const OVERTIME_HOURS = /([\d.,]+) overtime hours?/

/**
 * What an option costs against the plan. Overtime is paid (1.5x for employees), so it always
 * shows its hours and never "no extra cost": when it costs nothing extra overall, that is because
 * it replaces other paid hours, and the text says so. With no plan to compare against (an
 * infeasible run, `wholePlan`), the cost is the whole plan's.
 */
export function optionCost(option: PricedOption, wholePlan = false): string {
  if (option.costCents == null) return 'cost not priced'
  const hours = option.id === 'overtime' ? OVERTIME_HOURS.exec(option.detail)?.[1] : undefined
  const paid = hours ? `${hours} paid overtime hours \u00B7 ` : ''
  if (wholePlan) return `${paid}about ${formatMoney(option.costCents)} in total`
  if (option.costCents > 0) return `${paid}+${formatMoney(option.costCents)} extra`
  if (option.id === 'overtime') {
    const total = option.costCents < 0 ? `${formatMoney(-option.costCents)} less in total` : 'same total cost'
    return `${paid}${total}: the overtime replaces other paid hours`
  }
  return option.costCents < 0 ? `${formatMoney(-option.costCents)} less` : 'no extra cost'
}

/** What "Accept" can do right now: apply, wait for something first (and why), or already done. */
export type AcceptState =
  | { kind: 'ready'; applying: boolean; onAccept: () => void }
  | { kind: 'blocked'; reason: string }
  | { kind: 'applied'; version: number }

const REASON_ID = 'keep-date-accept-why'
const STARTING_ID = 'keep-date-starting'

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
            <b>{optionCost(remedy)}</b>
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
