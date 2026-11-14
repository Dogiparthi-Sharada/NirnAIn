/**
 * "Constraint check": do any Must hold / Can flex choices need to change for this project to
 * finish as planned? (port of the mockup's views/optimize-advice.js)
 *
 * The confirmation sheet shows a one-line brief before the solver runs; the plan shows the
 * full card. Items come from `policyRisks()` (shared with Constraints) plus facts from the run
 * itself: peak load and budget headroom (summary), a go-live that moved past the deadline,
 * and a deadline that was only assumed hard. Levels: change = we recommend changing it,
 * check = confirm it is intended, decide = a trade-off only the project manager can make.
 */
import { Link } from 'react-router'
import { POLICY_KEYS, POLICY_LABEL, POLICY_NAME, policyOf, policyRisks, type PolicyInputs, type PolicyRisk } from '@/lib/constraintPolicy'
import { formatMoney } from '@/lib/format'
import type { PlanRun } from '@/lib/optimize'
import { stepPath, useStepNav } from '@/lib/steps'
import { daysBetween, pluralDays, shortDay } from './dates'

type AdviceLevel = 'change' | 'check' | 'decide'

interface AdviceItem {
  key: string
  level: AdviceLevel
  title: string
  why: string
  fix: string
}

const ADVICE_LEVEL: Record<AdviceLevel, { chipClass: string; label: string }> = {
  change: { chipClass: 'st-missing', label: 'Change recommended' },
  check: { chipClass: 'st-inferred', label: 'Worth checking' },
  decide: { chipClass: 'st-default', label: 'Your call' },
}

/** The policy risks, with the reasoning tied to this plan's peak load and budget headroom. */
function policyAdvice(policy: PolicyInputs, run: PlanRun): AdviceItem[] {
  const summary = run.result?.summary
  return policyRisks(policy).map((risk) => {
    let why = risk.why
    const peak = summary?.peakUtilization
    if (risk.card === 'capacity' && peak) {
      why +=
        peak.percent <= 100
          ? ` This plan peaks at ${peak.percent}%, so nobody is on overtime yet; the risk appears as soon as the schedule tightens.`
          : ` This plan already schedules ${peak.name} at ${peak.percent}% of their hours.`
    }
    if (risk.card === 'budget' && summary && summary.budgetCents != null && summary.costCents < summary.budgetCents) {
      why += ` This plan is ${formatMoney(summary.budgetCents - summary.costCents)} under budget, so holding it costs nothing here.`
    }
    return { key: risk.card, level: risk.level, title: risk.title, why, fix: risk.fix }
  })
}

/** Facts about the deadline: go-live moved past it (remedy or a deadline that can move), or it was only assumed hard. */
function deadlineAdvice(policy: PolicyInputs, run: PlanRun): AdviceItem[] {
  const finish = run.result?.summary?.completionDate
  const deadline = policy.mustFinishBy
  if (!finish) return []
  const movedByRemedy = run.options?.remedy === 'move_deadline'
  const daysLate = deadline ? daysBetween(deadline, finish) : 0
  if (movedByRemedy || daysLate > 0) {
    const after = deadline && daysLate > 0 ? `, ${pluralDays(daysLate)} after ${shortDay(deadline)}` : ''
    return [
      {
        key: 'moved',
        level: 'decide',
        title: `Go-live moved to ${shortDay(finish)}${after}`,
        why: movedByRemedy
          ? 'You chose to let the deadline move, so every later task shifts to fit the team you have.'
          : 'The deadline was allowed to move, so the plan finishes when the team you have can finish the work.',
        fix: deadline
          ? `If ${shortDay(deadline)} matters, add people or hours on step 5, then run again.`
          : 'If the date matters, set a deadline on step 5 and run again.',
      },
    ]
  }
  if (policy.deadlineAnswer === 'unknown' && deadline) {
    const spare = -daysLate
    return [
      {
        key: 'assumed-deadline',
        level: 'check',
        title: `${shortDay(deadline)} was assumed to be a hard deadline`,
        why: `You answered \u201CDon\u2019t know\u201D, so the safer default was used. ${
          spare > 0 ? `This plan meets it with ${pluralDays(spare)} to spare.` : 'This plan meets it exactly.'
        }`,
        fix: 'Confirm the date with the sponsor. Nothing changes if it is hard.',
      },
    ]
  }
  return []
}

/** "Budget: Must hold · Deadline: Must hold · …". */
function PolicySummary({ policy }: { policy: PolicyInputs }) {
  return (
    <>
      {POLICY_KEYS.map((key, position) => (
        <span key={key}>
          {position > 0 && ' \u00B7 '}
          {POLICY_NAME[key]}: <b>{POLICY_LABEL[policyOf(key, policy)]}</b>
        </span>
      ))}
    </>
  )
}

/** The "Constraint check" card on the plan, just under the KPIs. */
export function ConstraintCheckCard({ policy, run }: { policy: PolicyInputs; run: PlanRun }) {
  const navigateToStep = useStepNav()
  const items = [...policyAdvice(policy, run), ...deadlineAdvice(policy, run)]
  if (items.length === 0) {
    return (
      <div className="card advice ok">
        <div className="adv-top">
          <h3 style={{ margin: 0 }}>Constraint check</h3>
          <span className="chip st-stated">{'\u2713'} Nothing to change</span>
        </div>
        <p className="small" style={{ margin: '6px 0 0' }}>
          Your Must hold / Can flex choices fit this project and this plan. <PolicySummary policy={policy} />
        </p>
      </div>
    )
  }
  return (
    <div className="card advice">
      <div className="adv-top">
        <h3 style={{ margin: 0 }}>Constraint check</h3>
        <span className="small muted">
          {items.length} item{items.length > 1 ? 's' : ''} &middot; the plan is valid either way; these are about whether it tells the whole story
        </span>
      </div>
      {items.map((item) => (
        <div key={item.key} className={`adv ${item.level}`}>
          <div className="row">
            <span className={`chip ${ADVICE_LEVEL[item.level].chipClass}`}>{ADVICE_LEVEL[item.level].label}</span>
            <b>{item.title}</b>
          </div>
          <div className="adv-why">
            <b>Why:</b> {item.why}
          </div>
          <div className="adv-fix">
            <b>Suggested change:</b> {item.fix}
          </div>
        </div>
      ))}
      <div className="row between mt8">
        <span className="small muted">
          You chose: <PolicySummary policy={policy} />
        </span>
        <button className="btn sec sm" type="button" onClick={() => navigateToStep('constraints')}>
          Review on Constraints &rarr;
        </button>
      </div>
    </div>
  )
}

/** The one-line warning on the confirmation sheet when a setting is flagged before the run. */
export function ConstraintCheckBrief({ risks, projectId }: { risks: PolicyRisk[]; projectId: string }) {
  if (risks.length === 0) return null
  return (
    <div className="cf-banner warn mt">
      <b>
        {'\u26A0'} Constraint check: {risks.length} setting{risks.length > 1 ? 's' : ''} flagged.
      </b>
      <ul>
        {risks.map((risk) => (
          <li key={risk.card}>
            <b>{risk.title}.</b> {risk.fix}
          </li>
        ))}
      </ul>
      <span className="small">
        You can still run; the results page explains each one. <Link to={stepPath('constraints', projectId)}>Review on Constraints</Link>
      </span>
    </div>
  )
}
