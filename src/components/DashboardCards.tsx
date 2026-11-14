/**
 * The per-project dashboard cards: KPIs, delivery health, open risks, notifications and AI findings.
 *
 * Purpose: Never invent: values the project doesn't have yet show the design's dashes and
 * explanations ("Available after planning", "Not tracked yet") instead of numbers.
 * How it fits: Port of the mockup's scripts/views/dashboard.js (`#d-kpis`, `#d-health`,
 * `#d-risks`, `#d-notes`, `#d-findings`); rendered by `screens/Dashboard.tsx` from one project's
 * overview (`lib/overview.ts`); layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { Fragment } from 'react'
import { Link } from 'react-router'
import { formatDate, formatDateTime, formatMoney } from '@/lib/format'
import type { ProjectOverview } from '@/lib/overview'
import { stepPath } from '@/lib/steps'
import { STATUS_LABELS, type NotificationLevel } from '@/lib/types'

const DASH = '\u2014'
const LATER = 'Available after planning'
const LEVEL_COLORS: Record<NotificationLevel, string> = { info: '#4455aa', warn: '#a8691f', alert: '#A83A4A' }
/** Risk level chips as in the design's plan risks: High red, Medium amber. */
const RISK_CHIPS: Record<ProjectOverview['risks'][number]['level'], string> = { High: 'st-missing', Medium: 'st-inferred' }
/** Where each kind of AI finding comes from, so the PM can open it. */
const FINDING_ORIGINS = {
  wbs_validate: { step: 'wbs', label: 'From WBS validation' },
  optimization: { step: 'optimization', label: 'From the optimizer' },
} as const

/** One `.kpi` tile: label, big value, explanation. */
function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="kpi">
      <div className="k">{label}</div>
      <div className="v">{value}</div>
      <div className="s">{sub}</div>
    </div>
  )
}

/** Progress, budget, milestones and open risks. */
export function DashboardKpis({ overview }: { overview: ProjectOverview }) {
  const { project, plan, milestones, risks } = overview
  /** Formats cents in the project's currency. */
  const money = (cents: number) => formatMoney(cents, project.currency)
  const budgetSub =
    project.budgetCents == null
      ? 'Budget not set'
      : plan?.expectedCostCents != null
        ? `Planned cost ${money(plan.expectedCostCents)} \u00B7 ${money(project.budgetCents - plan.expectedCostCents)} headroom`
        : 'No plan cost yet'
  let milestoneValue = DASH
  let milestoneSub = LATER
  if (milestones.total === 0) milestoneSub = 'No milestones set'
  else if (plan) {
    milestoneValue = milestones.next ? milestones.next.status : 'All passed'
    milestoneSub = milestones.next ? `${milestones.next.name} \u00B7 ${formatDate(milestones.next.dueDate)}` : `${milestones.total} milestones`
  }
  return (
    <div className="kpis" id="d-kpis">
      <Kpi label="Progress" value={`${project.progressPct}%`} sub={plan ? `Plan v${plan.version} in force` : STATUS_LABELS[project.status]} />
      <Kpi
        label="Budget"
        value={project.budgetCents == null ? DASH : formatMoney(project.budgetCents, project.currency, { compact: true })}
        sub={budgetSub}
      />
      <Kpi label="Milestones" value={milestoneValue} sub={milestoneSub} />
      <Kpi
        label="Open risks"
        value={risks.length || plan ? String(risks.length) : DASH}
        sub={plan ? `From Plan v${plan.version} and the scope` : risks.length ? 'From the scope' : LATER}
      />
    </div>
  )
}

/** Status, dates, utilization, violations, constraint choices and spend (not tracked yet). */
export function DeliveryHealth({ overview }: { overview: ProjectOverview }) {
  const { project, plan, milestones, constraintChoices } = overview
  const rows: [string, string][] = [
    ['Status', STATUS_LABELS[project.status]],
    ['Due', project.dueDate ? formatDate(project.dueDate) : 'Not set'],
    ['Next milestone', milestones.next ? `${milestones.next.name} \u00B7 ${formatDate(milestones.next.dueDate)}` : DASH],
    ['Peak utilization', plan?.peakUtilization != null ? `${plan.peakUtilization}%` : DASH],
    ['Constraint violations', plan ? (plan.violations.soft ? `${plan.violations.hard} hard \u00B7 ${plan.violations.soft} soft` : String(plan.violations.hard)) : DASH],
    ['Constraint choices', constraintChoices.map((choice) => `${choice.name}: ${choice.label}`).join(' \u00B7 ')],
    ['Spent to date', overview.spentToDateCents != null ? formatMoney(overview.spentToDateCents, project.currency) : 'Not tracked yet'],
  ]
  return (
    <div className="card">
      <h3>Delivery health</h3>
      <div className="kv" id="d-health">
        {rows.map(([label, value]) => (
          <Fragment key={label}>
            <div className="k">{label}</div>
            <div className="v">{value}</div>
          </Fragment>
        ))}
      </div>
    </div>
  )
}

/** Open risks from the applied plan and the reviewed scope. */
export function OpenRisks({ overview }: { overview: ProjectOverview }) {
  const { risks, plan } = overview
  return (
    <div className="card">
      <div className="row between">
        <h3 style={{ margin: 0 }}>Open risks</h3>
        <span className="cnt" id="d-rcount">
          {risks.length || plan ? risks.length : ''}
        </span>
      </div>
      <div id="d-risks" className="mt8">
        {risks.length === 0 ? (
          <p className="small muted" style={{ margin: 0 }}>
            {plan ? `No open risks in Plan v${plan.version}.` : 'Risks appear once a plan is optimized and applied (step 6).'}
          </p>
        ) : (
          risks.map((risk, index) => (
            <div key={`${risk.text}-${index}`} className="risk">
              <span className={`chip ${RISK_CHIPS[risk.level]}`}>{risk.level}</span>
              <div>
                {risk.text}
                <small>{risk.source}</small>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

/** This project's notifications, newest first. */
export function ProjectNotifications({ overview }: { overview: ProjectOverview }) {
  const { notifications, notificationTotal } = overview
  return (
    <div className="card">
      <div className="row between">
        <h3 style={{ margin: 0 }}>Notifications</h3>
        <span className="cnt" id="d-ncount">
          {notificationTotal}
        </span>
      </div>
      <div id="d-notes" className="mt8">
        {notifications.length === 0 ? (
          <p className="small muted" style={{ margin: 0 }}>
            No notifications for this project yet.
          </p>
        ) : (
          notifications.slice(0, 6).map((notification) => (
            <div key={notification.id} className="notif">
              <span className="dot" style={{ background: LEVEL_COLORS[notification.level], marginTop: 5 }} aria-hidden="true" />
              <div>
                {notification.message}
                <small>{formatDateTime(notification.createdAt)}</small>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

/** AI findings from WBS validation and the optimizer's recommendations. */
export function AiFindings({ overview, projectId }: { overview: ProjectOverview; projectId: string }) {
  const { findings } = overview
  return (
    <div className="card">
      <h3>AI findings</h3>
      <div id="d-findings" className="small muted">
        {findings.length === 0
          ? 'AI findings appear after you run intake or optimization on this project.'
          : findings.map((finding, index) => (
              <div key={`${finding.source}-${index}`} className="notif">
                <span aria-hidden="true">&#10022;</span>
                <div>
                  {finding.text}
                  <small>
                    <Link to={stepPath(FINDING_ORIGINS[finding.source].step, projectId)}>{FINDING_ORIGINS[finding.source].label}</Link>
                  </small>
                </div>
              </div>
            ))}
      </div>
    </div>
  )
}
