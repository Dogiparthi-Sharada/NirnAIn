/**
 * The WBS table of the WBS Builder (mockup views/wbs.js `#wbs-tbl`): project, deliverables and work
 * packages with code, name, duration and owner, as an indented tree ("Chart") or a flat list.
 *
 * Each work package shows its weeks and an estimate chip — "AI estimate" with a Confirm button
 * until the PM confirms it, "✓ Confirmed", or "Yours" for estimates the PM entered (see
 * `lib/wbs.ts`), and the skills it needs as chips under its name (`WbsSkillChips`). Rows can be
 * edited inline (name, weeks, owner, skills; see `WbsEditCells`, which checks the values first) and
 * reordered within their parent by dragging or Alt+↑/↓; the screen performs the API calls.
 */
import { useState, type DragEvent, type KeyboardEvent } from 'react'
import { displayCode, estimateState, formatWeeks, type EstimateState, type SkillOptions, type WbsTreeNode } from '@/lib/wbs'
import type { WbsNodePatch } from '@/lib/types'
import WbsEditCells from './WbsEditCells'
import { WbsSkillChips } from './WbsSkillPicks'

const CHIPS: Record<EstimateState, [className: string, label: string]> = {
  ai: ['st-inferred', 'AI estimate'],
  confirmed: ['st-confirmed', '✓ Confirmed'],
  yours: ['st-yours', 'Yours'],
  missing: ['st-missing', 'No estimate'],
}

/** Id of a work package's Confirm button (or Edit button when it has no estimate); gate links focus it. */
export function estimateControlId(nodeId: string): string {
  return `wbs-ok-${nodeId}`
}

/** Rows of the WBS with inline editing, estimate confirmation and reordering. */
export default function WbsTable({
  nodes,
  flat,
  collapsed,
  onToggle,
  owners,
  skillOptions,
  confirming,
  onConfirm,
  onSave,
  onAddChild,
  onMove,
  onSelect,
  selectedId,
}: {
  nodes: WbsTreeNode[]
  /** "List" view: no indentation of work packages. */
  flat: boolean
  /** Deliverable ids whose work packages are hidden. */
  collapsed: ReadonlySet<string>
  onToggle: (deliverableId: string) => void
  owners: { id: string; name: string }[]
  /** The project's skill list for the chips and the edit row's picker. */
  skillOptions: SkillOptions
  /** Ids whose confirmation is in flight. */
  confirming: ReadonlySet<string>
  onConfirm: (nodeId: string) => void
  onSave: (node: WbsTreeNode, patch: WbsNodePatch) => void
  /** Adds a deliverable under the project row or a work package under a deliverable. */
  onAddChild: (parent: WbsTreeNode) => void
  /** Moves `source` to the position of `target` (same parent only). */
  onMove: (source: WbsTreeNode, target: WbsTreeNode) => void
  onSelect: (node: WbsTreeNode) => void
  selectedId: string | null
}) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [dropTargetId, setDropTargetId] = useState<string | null>(null)

  const byId = new Map(nodes.map((node) => [node.id, node]))
  const visible = nodes.filter((node) => node.level < 3 || !node.parentId || !collapsed.has(node.parentId))

  /** Keyboard alternative to dragging: Alt+↑/↓ moves a row within its parent. */
  function rowKeys(event: KeyboardEvent<HTMLTableRowElement>, node: WbsTreeNode) {
    if (!event.altKey || node.level === 1 || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
    event.preventDefault()
    const siblings = nodes.filter((candidate) => candidate.parentId === node.parentId)
    const index = siblings.findIndex((candidate) => candidate.id === node.id)
    if (event.key === 'ArrowUp' && index > 0) onMove(node, siblings[index - 1])
    if (event.key === 'ArrowDown' && index < siblings.length - 1) onMove(siblings[index + 1], node)
  }

  function dragProps(node: WbsTreeNode) {
    if (node.level === 1 || editingId === node.id) return {}
    return {
      draggable: true,
      onDragStart: (event: DragEvent) => {
        setDraggedId(node.id)
        event.dataTransfer.effectAllowed = 'move'
      },
      onDragEnd: () => {
        setDraggedId(null)
        setDropTargetId(null)
      },
      onDragOver: (event: DragEvent) => {
        const dragged = draggedId ? byId.get(draggedId) : undefined
        if (!dragged || dragged.id === node.id || dragged.parentId !== node.parentId) return
        event.preventDefault()
        setDropTargetId(node.id)
      },
      onDrop: (event: DragEvent) => {
        event.preventDefault()
        const dragged = draggedId ? byId.get(draggedId) : undefined
        if (dragged) onMove(dragged, node)
        setDraggedId(null)
        setDropTargetId(null)
      },
    }
  }

  return (
    <table className={flat ? 'tbl wbs flat' : 'tbl wbs'} id="wbs-tbl">
      <thead>
        <tr>
          <th style={{ width: 80 }}>Code</th>
          <th>Name</th>
          <th style={{ width: 220 }}>Duration</th>
          <th style={{ width: 120 }}>Owner</th>
          <th style={{ width: 110 }}>
            <span className="sr-only">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody id="wbs-body">
        {visible.map((node) => {
          const editing = editingId === node.id
          const state = estimateState(node)
          const [chipClass, chipLabel] = CHIPS[state]
          return (
            <tr
              key={node.id}
              className={`l${node.level}`}
              tabIndex={node.level > 1 ? 0 : undefined}
              aria-keyshortcuts={node.level > 1 ? 'Alt+ArrowUp Alt+ArrowDown' : undefined}
              onKeyDown={(event) => rowKeys(event, node)}
              onClick={() => onSelect(node)}
              style={{
                boxShadow: dropTargetId === node.id ? 'inset 0 2px var(--mfg)' : undefined,
                opacity: draggedId === node.id ? 0.45 : undefined,
                background: selectedId === node.id && node.level > 1 ? 'var(--soft)' : undefined,
                cursor: node.level > 1 && !editing ? 'grab' : undefined,
              }}
              {...dragProps(node)}
            >
              <td className="mono">
                {node.level === 2 && (
                  <button
                    className="tog"
                    type="button"
                    aria-expanded={!collapsed.has(node.id)}
                    aria-label={`${collapsed.has(node.id) ? 'Show' : 'Hide'} the work packages of ${displayCode(node)}`}
                    onClick={(event) => {
                      event.stopPropagation()
                      onToggle(node.id)
                    }}
                  >
                    {collapsed.has(node.id) ? '\u25B8' : '\u25BE'}
                  </button>
                )}
                {displayCode(node)}
              </td>
              {editing ? (
                <WbsEditCells
                  node={node}
                  owners={owners}
                  skillOptions={skillOptions}
                  onSave={(patch) => {
                    onSave(node, patch)
                    setEditingId(null)
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : (
                <>
                  <td>
                    {node.name}
                    {node.level === 3 && <WbsSkillChips skills={node.skills} projectSkills={skillOptions.skills} />}
                  </td>
                  <td>
                    {node.level === 3 && (
                      <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        {state !== 'missing' && <span className="mono">{formatWeeks(node.durationDays)}</span>}
                        <span className={`chip ${chipClass}`}>{chipLabel}</span>
                        {state === 'ai' && (
                          <button
                            className="btn sec sm"
                            id={estimateControlId(node.id)}
                            type="button"
                            disabled={confirming.has(node.id)}
                            aria-label={`Confirm ${formatWeeks(node.durationDays)} for ${node.name}`}
                            onClick={(event) => {
                              event.stopPropagation()
                              onConfirm(node.id)
                            }}
                          >
                            {confirming.has(node.id) ? 'Confirming…' : 'Confirm'}
                          </button>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="small">{node.level === 3 && (node.ownerName ?? <span className="muted">Unassigned</span>)}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button
                      className="linkbtn"
                      type="button"
                      id={state === 'missing' && node.level === 3 ? estimateControlId(node.id) : undefined}
                      aria-label={`Edit ${node.name}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        setEditingId(node.id)
                      }}
                    >
                      Edit
                    </button>
                    {node.level < 3 && (
                      <>
                        {' · '}
                        <button
                          className="linkbtn"
                          type="button"
                          aria-label={node.level === 1 ? 'Add a deliverable' : `Add a work package to ${node.name}`}
                          onClick={(event) => {
                            event.stopPropagation()
                            onAddChild(node)
                          }}
                        >
                          {node.level === 1 ? '+ Deliverable' : '+ Package'}
                        </button>
                      </>
                    )}
                  </td>
                </>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
