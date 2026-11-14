/**
 * The Budget and Deadline cards on step 5: the money and date limits, each with its Must hold / Can flex switch.
 *
 * Purpose: Every field saves when the PM leaves it. A budget amount follows step 1's rules (more
 * than $0, at most $100B) and stays in the field with the reason when it breaks them; other values
 * the server would reject are explained in a toast and revert. Chips say where a value came from
 * (Stated, Yours, Default), and the Deadline switch is the deadline question.
 * How it fits: Port of the mockup's views/constraints.js; rendered by `screens/Constraints.tsx`;
 * uses `CommitInput`, `PolicyControls` and the number rules in `lib/constraints.ts`;
 * layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useState } from 'react'
import { Chip, type ChipKind } from '@/components/InputsKit'
import { policyOf, type PolicyInputs, type PolicyRisk } from '@/lib/constraintPolicy'
import { dollarsText, moneyLimitProblem, parseDollars, parsePercent, policyChanges, type ConstraintChanges } from '@/lib/constraints'
import type { ConstraintInputs } from '@/lib/inputs'
import type { InputSource } from '@/lib/optimize'
import { toast } from '@/lib/toast'
import type { Milestone } from '@/lib/types'
import { CommitInput } from './CommitInput'
import { PolicyNotes, PolicyToggle } from './PolicyControls'

interface CardProps {
  constraints: ConstraintInputs
  policy: PolicyInputs
  risks: PolicyRisk[]
  onSave: (changes: ConstraintChanges) => void
}

const SOURCE_CHIP: Record<InputSource, ChipKind> = {
  stated: 'stated',
  confirmed: 'confirmed',
  needs_you: 'missing',
  default: 'default',
  assumed: 'assumed',
  yours: 'yours',
}

type MoneyField = 'budgetTotalCents' | 'laborCapCents' | 'infraCapCents'

/** The Budget card: total, contingency and the optional labor and infrastructure caps. */
export function BudgetCard({ constraints, policy, risks, onSave, budgetSource }: CardProps & { budgetSource: InputSource | undefined }) {
  // An amount that can't be saved stays in its field with the reason underneath, as on step 1.
  const [moneyProblems, setMoneyProblems] = useState<Partial<Record<MoneyField, string>>>({})

  /** Shows (or clears) the reason a money field can't be saved. */
  function showMoneyProblem(field: MoneyField, problem: string | null) {
    setMoneyProblems((previous) => ({ ...previous, [field]: problem ?? undefined }))
  }

  /** Saves a money limit, or keeps the typed amount with its problem (returns false). */
  function commitMoney(field: MoneyField, text: string): boolean {
    const problem = moneyLimitProblem(text)
    showMoneyProblem(field, problem)
    if (problem) return false
    onSave({ [field]: parseDollars(text) })
    return true
  }

  /** Saves the contingency percentage, or explains in a toast why it can't. */
  function commitContingency(text: string) {
    const percent = parsePercent(text)
    if (Number.isNaN(percent)) toast('Contingency must be a percentage from 0 to 100.')
    else onSave({ contingencyPct: percent })
  }

  const hasBudget = constraints.budgetTotalCents != null
  const budgetChip = !hasBudget ? <Chip kind="default">Not set</Chip> : <Chip kind={budgetSource ? SOURCE_CHIP[budgetSource] : 'yours'} />
  /** One money input with its inline problem message. */
  const moneyField = (field: MoneyField, id: string) => {
    const problem = moneyProblems[field]
    return (
      <>
        <CommitInput
          className={problem ? 'in invalid' : 'in'}
          id={id}
          inputMode="decimal"
          placeholder="$ 0"
          value={dollarsText(constraints[field])}
          onCommit={(text) => commitMoney(field, text)}
          onRevert={() => showMoneyProblem(field, null)}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-err` : undefined}
        />
        {problem && (
          <p className="err" id={`${id}-err`} role="alert">
            {problem}
          </p>
        )}
      </>
    )
  }

  return (
    <div className="card" id="con-budget">
      <div className="card-head">
        <h3>Budget</h3>
        <PolicyToggle policyKey="budget" mode={policyOf('budget', policy)} onChange={(mode) => onSave(policyChanges('budget', mode))} />
      </div>
      <PolicyNotes policyKey="budget" policy={policy} risks={risks} />
      <div className="g2 mt">
        <div className="field">
          <label htmlFor="con-budget-total">Total Budget {budgetChip}</label>
          {moneyField('budgetTotalCents', 'con-budget-total')}
          {hasBudget && policy.budgetQuote && <div className="quote">&ldquo;{policy.budgetQuote}&rdquo;</div>}
        </div>
        <div className="field">
          <label htmlFor="con-contingency">
            Contingency Reserve <Chip kind={constraints.contingencyPct > 0 ? 'yours' : 'default'} />
          </label>
          <CommitInput className="in" id="con-contingency" inputMode="decimal" value={`${constraints.contingencyPct} %`} onCommit={commitContingency} />
        </div>
        <div className="field">
          <label htmlFor="con-labor-cap">
            Labor Cost Cap <Chip kind="default">Optional</Chip>
          </label>
          {moneyField('laborCapCents', 'con-labor-cap')}
        </div>
        <div className="field">
          <label htmlFor="con-infra-cap">
            Infrastructure Cap <Chip kind="default">Optional</Chip>
          </label>
          {moneyField('infraCapCents', 'con-infra-cap')}
        </div>
      </div>
      {constraints.contingencyPct === 0 && (
        <div className="warnbox" style={{ background: '#F5E3C8' }}>
          No contingency reserve is set, so 0% is used unless you change it.
        </div>
      )}
    </div>
  )
}

const WORKING_DAY_CHOICES = [4, 5, 6]

/** "5 days (Mon–Fri)" or "N days". */
function workingDaysLabel(days: number): string {
  return days === 5 ? '5 days (Mon\u2013Fri)' : `${days} days`
}

/** The Must-Finish-By chip follows the deadline question: needs you, assumed, or confirmed. */
function finishChip(constraints: ConstraintInputs) {
  if (!constraints.mustFinishBy) return <Chip kind="default">Not set</Chip>
  if (constraints.deadlineAnswer == null) return <Chip kind="missing">Needs you</Chip>
  return <Chip kind={constraints.deadlineAnswer === 'unknown' ? 'assumed' : 'confirmed'} />
}

/** The Deadline card: start, must-finish-by, phase milestones and working days per week. */
export function DeadlineCard({ constraints, policy, risks, onSave }: CardProps) {
  const startDate = constraints.startDate
  const finishDate = constraints.mustFinishBy

  /**
   * A date can be changed but not removed: without one the server falls back to the project's own
   * dates, so an empty field would only hide the date the plan really uses.
   */
  function commitDates(start: string | null, finish: string | null) {
    if ((startDate && !start) || (finishDate && !finish)) toast('Pick a date. To let the deadline move, answer \u201CCan move\u201D above.')
    else if (start && finish && finish < start) toast('The must-finish-by date can\u2019t be before the start date.')
    else onSave({ startDate: start, mustFinishBy: finish })
  }

  /** Saves a milestone's new due date; a milestone can't lose its date. */
  function commitMilestone(position: number, dueDate: string) {
    if (!dueDate) {
      toast('A milestone needs a date.')
      return
    }
    const milestones: Milestone[] = constraints.milestones.map((milestone, index) => (index === position ? { ...milestone, dueDate } : milestone))
    onSave({ milestones })
  }

  const dayChoices = WORKING_DAY_CHOICES.includes(constraints.workingDaysPerWeek)
    ? WORKING_DAY_CHOICES
    : [...WORKING_DAY_CHOICES, constraints.workingDaysPerWeek].sort((first, second) => first - second)

  return (
    <div className="card" id="con-deadline">
      <div className="card-head">
        <h3>Deadline</h3>
        <PolicyToggle policyKey="deadline" mode={policyOf('deadline', policy)} onChange={(mode) => onSave(policyChanges('deadline', mode))} />
      </div>
      <PolicyNotes policyKey="deadline" policy={policy} risks={risks} />
      <div className="g2 mt">
        <div className="field">
          <label htmlFor="con-start">Project Start Date {startDate ? <Chip kind="yours" /> : <Chip kind="default">Not set</Chip>}</label>
          <CommitInput className="in" id="con-start" type="date" value={startDate ?? ''} onCommit={(text) => commitDates(text || null, finishDate)} />
        </div>
        <div className="field">
          <label htmlFor="con-finish">Must-Finish-By Date {finishChip(constraints)}</label>
          <CommitInput className="in" id="con-finish" type="date" value={finishDate ?? ''} onCommit={(text) => commitDates(startDate, text || null)} />
        </div>
        {constraints.milestones.map((milestone, position) => (
          <div className="field" key={milestone.id ?? `${milestone.name}-${position}`}>
            <label htmlFor={`con-milestone-${position}`}>
              {milestone.name} <Chip kind="default">{milestone.mode === 'soft' ? 'Soft' : 'Hard'} milestone</Chip>
            </label>
            <CommitInput className="in" id={`con-milestone-${position}`} type="date" value={milestone.dueDate} onCommit={(text) => commitMilestone(position, text)} />
          </div>
        ))}
      </div>
      <div className="field mt">
        <label htmlFor="con-working-days">
          Working Days per Week <Chip kind={constraints.workingDaysPerWeek === 5 ? 'default' : 'yours'} />
        </label>
        <select
          className="in"
          id="con-working-days"
          value={constraints.workingDaysPerWeek}
          onChange={(event) => onSave({ workingDaysPerWeek: Number(event.target.value) })}
        >
          {dayChoices.map((days) => (
            <option key={days} value={days}>
              {workingDaysLabel(days)}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
