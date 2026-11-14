/**
 * Small design pieces shared by the planning screens: provenance chips, gate and plan-in-force banners, the screen toast and the utilization bar.
 *
 * Purpose: Steps 3–6 must explain themselves the same way: a blocked Continue lists what is
 * missing as links, and an applied plan says that edits take effect on the next run.
 * How it fits: Used by `screens/ScopeReview.tsx`, `screens/WBSBuilder.tsx`,
 * `screens/Constraints.tsx`, `screens/OptimizationResults.tsx` and their parts; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
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

/** A provenance chip; `children` replaces the default label. */
export function Chip({ kind, children }: { kind: ChipKind; children?: ReactNode }) {
  const [className, label] = CHIP[kind]
  return <span className={`chip ${className}`}>{children ?? label}</span>
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
            {items.map((item, index) => (
              <li key={index}>
                {item.target || item.onClick ? (
                  <a
                    href="#"
                    onClick={(event) => {
                      event.preventDefault()
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

const TOAST_MS = 3800

/**
 * A screen's own short message at the bottom of the screen for about four seconds (the mockup's
 * toast). `keepMounted` keeps the hidden live region in the DOM between messages.
 */
export function useToast({ keepMounted = true }: { keepMounted?: boolean } = {}): [ReactNode, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  /** Shows `text`, replacing any message on screen, and hides it after a few seconds. */
  const show = useCallback((text: string) => {
    setMessage(text)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMessage(null), TOAST_MS)
  }, [])
  useEffect(() => () => window.clearTimeout(timer.current), [])
  if (!keepMounted && !message) return [null, show]
  const node = (
    <div className="toast" role="status" aria-live="polite" hidden={keepMounted ? !message : undefined}>
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

/** The breadcrumb from the dashboard to the current step. */
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
