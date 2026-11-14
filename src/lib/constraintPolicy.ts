/**
 * Must hold / Can flex: recommendations and risky combinations
 * (port of the approved mockup's scripts/rules/constraint-policy.js).
 *
 * The project manager decides whether each constraint is a hard limit; the solver follows.
 * This module recommends a default per constraint and flags combinations that would make the
 * plan misleading. Constraints shows a warning next to the card it concerns; Optimization
 * Results repeats the same items in its "Constraint check" card.
 *
 * Why "hard deadline + flexible capacity" is flagged: it is valid maths, but the solver then
 * meets the date by booking overtime, so the plan looks on time while people are scheduled
 * above their hours — exactly the slip the PM needs to see.
 */
import { formatMoney } from './format'

export type PolicyKey = 'budget' | 'deadline' | 'capacity' | 'skills'
export type PolicyMode = 'hard' | 'soft'
export type DeadlineAnswer = 'hard' | 'soft' | 'unknown' | null

export const POLICY_KEYS: PolicyKey[] = ['budget', 'deadline', 'capacity', 'skills']
export const POLICY_NAME: Record<PolicyKey, string> = {
  budget: 'Budget',
  deadline: 'Deadline',
  capacity: 'Employee capacity',
  skills: 'Required skills',
}
export const POLICY_LABEL: Record<PolicyMode, string> = { hard: 'Must hold', soft: 'Can flex' }

/** What the policy rules need to know about the project. */
export interface PolicyInputs {
  budgetMode: PolicyMode
  capacityMode: PolicyMode
  skillMode: PolicyMode
  deadlineAnswer: DeadlineAnswer
  budgetTotalCents: number | null
  /** ISO date, e.g. "2025-09-30". */
  mustFinishBy: string | null
  /** Required skills nobody on the team has. */
  skillGaps: string[]
  /** Source sentence for the budget, when the notes state it (shown in quotes). */
  budgetQuote?: string | null
}

export interface PolicyHelp {
  rec: PolicyMode | null
  recWhy: string
  hard: string
  soft: string
}

export interface PolicyRisk {
  card: PolicyKey
  /** 'change' = we recommend changing it; 'check' = confirm it is intended. */
  level: 'change' | 'check'
  title: string
  why: string
  fix: string
}

function money(cents: number | null): string {
  return cents == null ? 'the budget' : formatMoney(cents, 'USD')
}

function longDate(iso: string | null): string {
  if (!iso) return 'the deadline'
  const day = new Date(`${iso}T00:00:00`)
  return day.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function shortDate(iso: string | null): string {
  if (!iso) return 'the deadline'
  const day = new Date(`${iso}T00:00:00`)
  return day.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** 'hard' or 'soft' for one constraint. The deadline is hard unless the PM said it can move. */
export function policyOf(key: PolicyKey, inputs: PolicyInputs): PolicyMode {
  if (key === 'deadline') return inputs.deadlineAnswer === 'soft' ? 'soft' : 'hard'
  if (key === 'budget') return inputs.budgetMode
  if (key === 'capacity') return inputs.capacityMode
  return inputs.skillMode
}

/** What each choice means for the solver, and the recommended default with its reason. */
export function policyHelp(key: PolicyKey, inputs: PolicyInputs): PolicyHelp {
  const budget = money(inputs.budgetTotalCents)
  const date = longDate(inputs.mustFinishBy)
  switch (key) {
    case 'budget':
      return {
        rec: 'hard',
        recWhy: inputs.budgetQuote
          ? `Your notes say \u201C${inputs.budgetQuote}\u201D, so treat it as a ceiling.`
          : 'An approved budget is usually a ceiling; keep it unless the sponsor agreed to more.',
        hard: `The plan must cost ${budget} or less. If that is impossible, you get priced options instead of a plan.`,
        soft: `The plan may cost more than ${budget}, with a penalty, if that is the only way to meet the other limits.`,
      }
    case 'deadline':
      return {
        rec: null,
        recWhy: `Set by your answer to \u201CIs ${date} a hard deadline?\u201D above.`,
        hard: `Go-live must be on or before ${date}.`,
        soft: `Go-live may move past ${date} if that is the only way.`,
      }
    case 'capacity':
      return {
        rec: 'hard',
        recWhy: 'Overtime hides schedule risk. Holding hours makes a slip show up as a slip.',
        hard: 'Nobody is scheduled beyond the hours per week entered below.',
        soft: 'People may be scheduled up to 25% above their hours (overtime), with a penalty.',
      }
    case 'skills':
      return {
        rec: 'hard',
        recWhy: 'Work given to someone without the skill is the most common hidden delay.',
        hard: 'Work goes only to people who have the skill, or to an outside specialist if you allow one.',
        soft: 'Work may go to someone without the skill; ramp-up time is added to their tasks.',
      }
  }
}

/** Risky Must hold / Can flex combinations for the current inputs, in display order. */
export function policyRisks(inputs: PolicyInputs): PolicyRisk[] {
  const out: PolicyRisk[] = []
  const date = shortDate(inputs.mustFinishBy)
  if (policyOf('deadline', inputs) === 'hard' && policyOf('capacity', inputs) === 'soft') {
    out.push({
      card: 'capacity',
      level: 'change',
      title: 'Hold employee capacity when the deadline must hold',
      why: `With a fixed deadline and flexible hours, the solver meets ${date} by booking overtime instead of telling you the date is at risk. The plan looks on time while people are scheduled above their hours, and that is where projects really slip.`,
      fix: `Set Employee capacity to Must hold. If ${date} then cannot be met, you see it now, with priced options (a specialist, more hours, or a later date).`,
    })
  }
  if (policyOf('skills', inputs) === 'soft' && inputs.skillGaps.length > 0) {
    out.push({
      card: 'skills',
      level: 'change',
      title: 'Hold required skills while there is a skill gap',
      why: `Nobody on the team has ${inputs.skillGaps.join(', ')}. With skills set to Can flex, the solver may give that work to someone without the skill and only add ramp-up time, which is usually underestimated.`,
      fix: 'Set Required skills to Must hold, then allow an outside specialist or add someone who has the skill.',
    })
  }
  if (policyOf('budget', inputs) === 'soft') {
    const budget = money(inputs.budgetTotalCents)
    out.push({
      card: 'budget',
      level: 'check',
      title: `Confirm the budget may go over ${budget}`,
      why: inputs.budgetQuote
        ? `Your notes say \u201C${inputs.budgetQuote}\u201D. Letting it flex means a plan could cost more than was approved.`
        : 'Letting the budget flex means a plan could cost more than was approved.',
      fix: 'Keep Budget at Must hold unless the sponsor has agreed to more.',
    })
  }
  return out
}
