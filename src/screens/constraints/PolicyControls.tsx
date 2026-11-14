/**
 * The Must hold / Can flex controls of the Budget, Deadline, Team & capacity and Required skills cards.
 *
 * Purpose: The PM decides because it is a business call: each card explains the current choice,
 * shows the recommended setting and why, and warns when the combination would make the plan
 * misleading, with the same wording as Optimization Results.
 * How it fits: Port of the mockup's views/constraints.js `renderPolicy`; used by the step 5 cards;
 * wording and warnings from `lib/constraintPolicy.ts`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { POLICY_LABEL, POLICY_NAME, policyHelp, policyOf, type PolicyInputs, type PolicyKey, type PolicyMode, type PolicyRisk } from '@/lib/constraintPolicy'

const MODES: PolicyMode[] = ['hard', 'soft']

/** The two-button switch in a card header. `onChange` is not called for the current choice. */
export function PolicyToggle({ policyKey, mode, onChange }: { policyKey: PolicyKey; mode: PolicyMode; onChange: (mode: PolicyMode) => void }) {
  return (
    <span className="pol" data-pol={policyKey} role="group" aria-label={`${POLICY_NAME[policyKey]}: must hold or can flex`}>
      {MODES.map((choice) => (
        <button
          key={choice}
          type="button"
          data-polv={choice}
          className={mode === choice ? 'on' : undefined}
          aria-pressed={mode === choice}
          onClick={() => {
            if (choice !== mode) onChange(choice)
          }}
        >
          {POLICY_LABEL[choice]}
        </button>
      ))}
    </span>
  )
}

/**
 * What the current choice means for the solver, the recommendation (the deadline has none: it
 * follows the deadline question), and the warning for a risky combination on this card.
 */
export function PolicyNotes({ policyKey, policy, risks }: { policyKey: PolicyKey; policy: PolicyInputs; risks: PolicyRisk[] }) {
  const mode = policyOf(policyKey, policy)
  const help = policyHelp(policyKey, policy)
  const risk = risks.find((item) => item.card === policyKey)
  return (
    <>
      <div className="pol-help" id={`pol-${policyKey}-help`}>
        <b>{POLICY_LABEL[mode]}:</b> {help[mode]}
        <br />
        {help.rec ? (
          <>
            <span className={help.rec === mode ? 'rec' : 'rec off'}>Recommended: {POLICY_LABEL[help.rec]}</span> {help.recWhy}
          </>
        ) : (
          <>
            <span className="rec">Linked to the question above</span> {help.recWhy}
          </>
        )}
      </div>
      {risk && (
        <div className="pol-warn" id={`pol-${policyKey}-warn`}>
          <b>&#9888; {risk.title}.</b> {risk.why}
          <br />
          <b>Suggested:</b> {risk.fix}
        </div>
      )}
    </>
  )
}
