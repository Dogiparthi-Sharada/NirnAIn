/**
 * Small pieces of the approved design shared by Scope Review, WBS Builder and Constraints:
 * provenance chips, the step-gate banner, the plan-in-force banner, the toast and the utilization
 * bar.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { usePlanVersions, versionInForce } from '@/lib/inputs'
import { stepPath } from '@/lib/steps'

const CHIP = {
  stated: ['st-stated', 'Stated'],
  confirmed: ['st-confirmed', '\u2713 Confirmed'],
  inferred: ['st-inferred', 'Inferred'],
  assumed: ['st-assumed', 'Assumed'],
  missing: ['st-missing', 'Not found'],
  flag: ['st-missing', 'Not in notes'],
  default: ['st-default', 'Default'],
  yours: ['st-yours', 'Yours'],
  edited: ['st-yours', 'Edited by you'],
  estimate: ['st-inferred', 'AI estimate'],
} as const

export type ChipKind = keyof typeof CHIP

export function Chip({ kind, children }: { kind: ChipKind; children?: ReactNode }) {
  const [cls, label] = CHIP[kind]
  return <span className={`chip ${cls}`}>{children ?? label}</span>
}

/** Moves focus to an element of the page (a gate link target). */
export function jumpTo(selector: string) {
  const target = document.querySelector<HTMLElement>(selector)
  if (!target) return
  target.scrollIntoView({ block: 'center', behavior: 'smooth' })
  target.focus({ preventScroll: true })
}

export interface GateItem {
  label: ReactNode
  /** CSS selector to jump to, or a click handler. Plain text when neither is given. */
  target?: string
  onClick?: () => void
}

/**
 * Step gates never disable Continue: a blocked click opens this red banner listing each
 * blocker (it shrinks and closes as they are fixed). `focusKey` changes on every blocked click.
 */
export function GateBanner({ id, lead, items, open, focusKey }: {
  id: string
  lead: string
  items: GateItem[]
  open: boolean
  focusKey: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const visible = open && items.length > 0
  useEffect(() => {
    if (focusKey > 0 && ref.current && !ref.current.hidden) {
      ref.current.scrollIntoView({ block: 'center', behavior: 'smooth' })
      ref.current.focus({ preventScroll: true })
    }
  }, [focusKey])
  return (
    <div className="cf-banner bad mt" id={id} role="alert" tabIndex={-1} ref={ref} hidden={!visible}>
      {visible && (
        <>
          <b>&#9888; {lead}</b>
          <ul>
            {items.map((item, i) => (
              <li key={i}>
                {item.target || item.onClick ? (
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      if (item.onClick) item.onClick()
                      else if (item.target) jumpTo(item.target)
                    }}
                  >
                    {item.label}
                  </a>
                ) : (
                  item.label
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/** A short message at the bottom of the screen for about 4 seconds. */
export function useToast(): [ReactNode, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const show = useCallback((text: string) => {
    setMessage(text)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMessage(null), 3800)
  }, [])
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const node = (
    <div className="toast" role="status" aria-live="polite" hidden={!message}>
      {message}
    </div>
  )
  return [node, show]
}

/** Utilization bar: wine up to 80%, amber above 80%, red at 100% or more. */
export function UtilBar({ pct }: { pct: number }) {
  const color = pct >= 100 ? '#A83A4A' : pct > 80 ? '#a8691f' : '#722F37'
  return (
    <span className="ub">
      <span className="bar">
        <i style={{ width: `${Math.min(pct, 100)}%`, background: color }} />
      </span>
      {pct}%
    </span>
  )
}

/**
 * Info banner on the input steps once a plan is applied. Inputs stay editable: a change takes
 * effect when the PM re-runs step 6 and applies the result as the next version, and step 7 records
 * why a plan changed. Shows nothing before the first plan, while the versions load, or when they
 * can't be loaded (it informs; it never blocks).
 */
export function PlanInForceBanner({ projectId, id }: { projectId: string; id: string }) {
  const versions = usePlanVersions(projectId)
  const version = versionInForce(versions.data)
  if (version === null) return null
  return (
    <div className="banner" id={id} role="status">
      <b>Plan v{version} is in force.</b> Changes here take effect when you re-run on{' '}
      <Link to={stepPath('optimization', projectId)}>step 6</Link> and apply it as Plan v{version + 1}. To record why the plan changed
      (meeting notes, before/after comparison), use <Link to={stepPath('changes', projectId)}>step 7</Link>.
    </div>
  )
}

export function Crumb({ trail }: { trail: string[] }) {
  return (
    <div className="crumb">
      <Link to="/dashboard">Dashboard</Link>
      {trail.map((step) => (
        <span key={step}> &rsaquo; {step}</span>
      ))}
    </div>
  )
}
