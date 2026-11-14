/**
 * The bottom of step 5 (approved mockup views/constraints.js `conGate`): the red `#con-banner`,
 * the `#con-hint` status line, Back to WBS Builder and Run Optimization.
 *
 * Gate rule: Run Optimization is never disabled. A click while inputs are missing opens the red
 * banner listing each one as a link that moves focus to where it is fixed (or opens step 4); the
 * banner shrinks as items are fixed and closes when none are left. The status line always says
 * what is missing, or that the inputs are ready and how many settings are flagged.
 */
import { GateBanner, type GateItem } from '@/components/InputsKit'
import type { GateBlocker } from '@/lib/constraints'

/** Banner, status line and the step buttons. `tries` counts Run clicks (each one refocuses the banner). */
export function ConstraintsGate({ blockers, flagged, tries, onRun, onBack, onOpenWbs }: {
  blockers: GateBlocker[]
  /** Must hold / Can flex settings we recommend changing. */
  flagged: number
  tries: number
  onRun: () => void
  onBack: () => void
  onOpenWbs: () => void
}) {
  const items: GateItem[] = blockers.map((blocker) => {
    const target = blocker.target
    if (target && 'selector' in target) return { label: blocker.label, target: target.selector }
    if (target) return { label: blocker.label, onClick: onOpenWbs }
    return { label: blocker.label }
  })
  const lead = `${blockers.length === 1 ? '1 thing needs' : `${blockers.length} things need`} your input before the optimizer can run:`

  let hint: string
  if (blockers.length > 0) hint = `To continue: ${blockers.map((blocker) => blocker.hint).join(', ')}.`
  else if (flagged > 0) hint = `Ready. ${flagged} constraint setting${flagged > 1 ? 's' : ''} flagged above; you can still run.`
  else hint = 'Ready. You will check the inputs before the solver runs.'

  return (
    <>
      <GateBanner id="con-banner" lead={lead} items={items} open={tries > 0} focusKey={tries} />
      <div className="row between mt">
        <button className="btn sec" type="button" onClick={onBack}>
          &larr; Back to WBS Builder
        </button>
        <div className="row">
          <span className={blockers.length > 0 ? 'gate bad' : 'gate'} id="con-hint" aria-live="polite">
            {hint}
          </span>
          <button className="btn" id="btn-to-opt" type="button" onClick={onRun}>
            Run Optimization &rarr;
          </button>
        </div>
      </div>
    </>
  )
}
