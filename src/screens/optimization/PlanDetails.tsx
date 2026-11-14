/**
 * The plan's detail tabs on Optimization Results: task reassignments, schedule changes and
 * resource utilization, straight from the run result (port of the tab card in the mockup's
 * views/optimize-plan.js). Tabs follow the WAI-ARIA pattern: arrow keys move between them.
 */
import { useRef, useState, type KeyboardEvent } from 'react'
import type { PlanResult } from '@/lib/optimize'

type DetailTab = 'reassignments' | 'schedule' | 'utilization'

const TABS: { key: DetailTab; label: string }[] = [
  { key: 'reassignments', label: 'Task Reassignments' },
  { key: 'schedule', label: 'Schedule Changes' },
  { key: 'utilization', label: 'Resource Utilization' },
]

/** Utilization bar: wine up to 80%, amber above 80%, red at 100% or more (as the mockup's `ubar`). */
function UtilizationBar({ percent }: { percent: number }) {
  const colour = percent >= 100 ? '#A83A4A' : percent > 80 ? '#a8691f' : '#722F37'
  return (
    <span className="ub">
      <span className="bar">
        <i style={{ width: `${Math.min(Math.max(percent, 0), 100)}%`, background: colour }} />
      </span>
      {percent}%
    </span>
  )
}

function EmptyTab({ children }: { children: string }) {
  return <p className="small muted">{children}</p>
}

function ReassignmentsTable({ result }: { result: PlanResult }) {
  if (result.reassignments.length === 0) return <EmptyTab>No reassignments needed: the current owners already fit the plan.</EmptyTab>
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>Task</th>
          <th>Current Assignment</th>
          <th>Recommended</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {result.reassignments.map((change) => (
          <tr key={change.task}>
            <td>{change.task}</td>
            <td>{change.current}</td>
            <td>
              <b>{change.recommended}</b>
            </td>
            <td className="small">{change.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ScheduleTable({ result }: { result: PlanResult }) {
  if (result.scheduleChanges.length === 0) return <EmptyTab>The schedule is unchanged from the baseline.</EmptyTab>
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>WBS</th>
          <th>Task</th>
          <th>Original</th>
          <th>Optimized</th>
          <th>Delta</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {result.scheduleChanges.map((change) => (
          <tr key={change.wbs}>
            <td className="mono">{change.wbs}</td>
            <td>{change.name}</td>
            <td className="small">{change.original}</td>
            <td className="small">
              <b>{change.optimized}</b>
            </td>
            <td className="mono">{change.delta}</td>
            <td className="small">{change.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function UtilizationTable({ result }: { result: PlanResult }) {
  if (result.utilization.length === 0) return <EmptyTab>No resource utilization to show.</EmptyTab>
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>Name</th>
          <th>Before</th>
          <th>After</th>
          <th>Change</th>
        </tr>
      </thead>
      <tbody>
        {result.utilization.map((person) => {
          const change = person.after - person.before
          return (
            <tr key={person.name}>
              <td>
                <b>{person.name}</b>
              </td>
              <td>
                <UtilizationBar percent={person.before} />
              </td>
              <td>
                <UtilizationBar percent={person.after} />
              </td>
              <td className="mono">
                {change > 0 ? '+' : ''}
                {change}%
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** The tab card under the timeline. */
export function PlanDetails({ result }: { result: PlanResult }) {
  const [activeTab, setActiveTab] = useState<DetailTab>('reassignments')
  const tabButtons = useRef<Record<DetailTab, HTMLButtonElement | null>>({ reassignments: null, schedule: null, utilization: null })

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (step === 0) return
    event.preventDefault()
    const nextTab = TABS[(currentIndex + step + TABS.length) % TABS.length].key
    setActiveTab(nextTab)
    tabButtons.current[nextTab]?.focus()
  }

  return (
    <div className="card mt">
      <div className="tabs" role="tablist" aria-label="Plan details">
        {TABS.map((tab, index) => (
          <button
            key={tab.key}
            ref={(element) => {
              tabButtons.current[tab.key] = element
            }}
            className={`tab${activeTab === tab.key ? ' on' : ''}`}
            type="button"
            role="tab"
            id={`plan-tab-${tab.key}`}
            aria-selected={activeTab === tab.key}
            aria-controls={`plan-pane-${tab.key}`}
            tabIndex={activeTab === tab.key ? 0 : -1}
            onClick={() => setActiveTab(tab.key)}
            onKeyDown={(event) => moveFocus(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tbl-scroll" role="tabpanel" id={`plan-pane-${activeTab}`} aria-labelledby={`plan-tab-${activeTab}`}>
        {activeTab === 'reassignments' && <ReassignmentsTable result={result} />}
        {activeTab === 'schedule' && <ScheduleTable result={result} />}
        {activeTab === 'utilization' && <UtilizationTable result={result} />}
      </div>
    </div>
  )
}
