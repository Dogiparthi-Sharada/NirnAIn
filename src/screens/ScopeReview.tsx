/**
 * Step 3, Scope Review: accept, edit or remove each item the AI extracted, by category.
 *
 * Purpose: The WBS is built from the accepted scope, so every item must be decided before
 * Continue; items quoted from the notes can be accepted in one click, removed items keep their
 * Undo after a reload (QA S-15) and a blank edit is refused with a reason (QA S-06).
 * How it fits: Route `projects/:projectId/scope` in `App.tsx`; uses its own scope list
 * (`scope/items.ts`), the gate rules (`scope/gate.ts`), the item rows (`scope/ItemRow.tsx`) and
 * `components/InputsKit.tsx`; layer: screen.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { Chip, Crumb, GateBanner, useToast } from '@/components/InputsKit'
import { errorMessage } from '@/lib/api'
import type { EvidenceScopeItem } from '@/lib/inputs'
import { useAddScopeItem, useLatestIntakeRun, useUpdateScopeItem } from '@/lib/queries'
import { useStepNav } from '@/lib/steps'
import { SCOPE_CATEGORIES, SCOPE_CATEGORY_LABELS, type ScopeCategory, type ScopeItemPatch } from '@/lib/types'
import { PlanInForceBanner } from './changes/InForceBanner'
import { scopeGate } from './scope/gate'
import { ScopeItemRow } from './scope/ItemRow'
import { useScopeList, useScopeListSync } from './scope/items'

// Shown when Done is pressed on an empty item: blank items can't be saved (QA S-06).
const BLANK_EDIT = 'Type the item’s text, or press Esc to keep it as it was. Use Remove if it no longer applies.'

const SECTION_COLOR: Record<ScopeCategory, string> = {
  objective: '#DDE7D0',
  requirement: '#E3DCCB',
  deliverable: '#F5E3C8',
  constraint: '#F3D9D5',
  risk: '#F5E3C8',
  missing_info: '#F3EADB',
}

/** Lists the scope by category with the gate to the WBS, the add-item rows and the removed group. */
export default function ScopeReview() {
  const goToStep = useStepNav()
  const { projectId = '' } = useParams()
  // Removed items are listed too, so they keep their Undo after a reload (QA S-15).
  const itemsQuery = useScopeList(projectId)
  const syncList = useScopeListSync(projectId)
  const intake = useLatestIntakeRun(projectId)
  const updateItem = useUpdateScopeItem(projectId)
  const addItem = useAddScopeItem(projectId)
  const [toast, showToast] = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [adding, setAdding] = useState<ScopeCategory | null>(null)
  const [addText, setAddText] = useState('')
  const [tried, setTried] = useState(0)

  const items = useMemo(() => (itemsQuery.data ?? []) as EvidenceScopeItem[], [itemsQuery.data])
  const visible = items.filter((item) => item.status !== 'removed')
  const removed = items.filter((item) => item.status === 'removed')
  const accepted = visible.filter((item) => item.status === 'accepted').length
  // Pending items (and, first, those saying more than the notes) block the WBS step.
  const gate = scopeGate(items, () => void acceptAllQuoted())
  const actionError = updateItem.error ?? addItem.error

  /** Saves a change to one item and merges the saved item into the list. */
  function patch(itemId: string, body: ScopeItemPatch) {
    updateItem.mutate({ itemId, patch: body }, { onSuccess: syncList })
  }

  /** Opens the editor on an item with its current text. */
  function startEdit(item: EvidenceScopeItem) {
    setEditing(item.id)
    setEditText(item.text)
    setEditError(null)
  }

  /** Closes the editor without saving. */
  function stopEdit() {
    setEditing(null)
    setEditError(null)
  }

  /** Saves the edited text (unchanged text is not sent); a blank text keeps the editor open with a reason. */
  function saveEdit(item: EvidenceScopeItem) {
    const text = editText.trim()
    if (!text) {
      setEditError(BLANK_EDIT)
      return
    }
    if (text !== item.text) patch(item.id, { text })
    stopEdit()
  }

  /** Adds the typed item to a category and closes the add row. */
  function saveAdd(category: ScopeCategory) {
    const text = addText.trim()
    if (!text) return
    addItem.mutate(
      { category, text },
      {
        onSuccess: (item) => {
          syncList(item)
          setAdding(null)
        },
      },
    )
  }

  /** The props of one item row: its state and the accept, remove, fix and edit actions. */
  function rowProps(item: EvidenceScopeItem) {
    return {
      item,
      editing: editing === item.id,
      editText,
      editError: editing === item.id ? editError : null,
      onEditText: (text: string) => {
        setEditText(text)
        setEditError(null)
      },
      onAccept: () => patch(item.id, { status: item.status === 'accepted' ? 'pending' : 'accepted' }),
      onRemove: () => patch(item.id, { status: item.status === 'removed' ? 'pending' : 'removed' }),
      onFix: () => item.suggestedText && patch(item.id, { text: item.suggestedText }),
      onEdit: () => (editing === item.id ? saveEdit(item) : startEdit(item)),
      onCancelEdit: stopEdit,
    }
  }

  /** Accepts every pending item quoted from the notes and says how many are left to check. */
  async function acceptAllQuoted() {
    const quoted = items.filter((item) => item.evidence === 'stated' && item.status === 'pending')
    const results = await Promise.allSettled(
      quoted.map((item) =>
        updateItem.mutateAsync({ itemId: item.id, patch: { status: 'accepted' } }).then((saved) => syncList(saved)),
      ),
    )
    const done = results.filter((result) => result.status === 'fulfilled').length
    const left = items.filter((item) => item.status === 'pending').length - done
    showToast(`Accepted ${done} items quoted from your notes. ${left} left for you to check.`)
  }

  /** Goes to the WBS Builder, or shows the gate banner when items are still undecided. */
  function continueToWbs() {
    if (gate.blocked) setTried((attempts) => attempts + 1)
    else goToStep('wbs')
  }

  const crumb = <Crumb trail={['Create Project', 'AI Intake', 'Scope Review']} />
  const header = (
    <div className="hdr">
      <div>
        <h1>Scope Review</h1>
        <div className="sub">Review AI-extracted project information. Accept, edit, or remove each item.</div>
        {intake.data?.model === 'rules' && (
          <p className="note">
            AI extraction is not configured, so these items come from the built-in rules extractor. Check each one
            against your notes.
          </p>
        )}
      </div>
      <div className="row">
        {visible.length > 0 && (
          <span className="small muted" id="sc-count">
            <b>{accepted}</b> / {visible.length} accepted
          </span>
        )}
        <button className="btn sec" id="accept-all" type="button" onClick={() => void acceptAllQuoted()} disabled={!items.length}>
          Accept All quoted
        </button>
      </div>
    </div>
  )

  if (itemsQuery.isPending || itemsQuery.isError) {
    return (
      <section className="view on" id="v-scope">
        <div className="pg">
          {crumb}
          {header}
          {itemsQuery.isPending ? (
            <div className="card center" aria-busy="true">
              <p className="muted small">Loading scope items…</p>
            </div>
          ) : (
            <div className="cf-banner bad" role="alert">
              <b>We couldn't load the scope.</b> {errorMessage(itemsQuery.error)}{' '}
              <button className="btn sec sm" type="button" onClick={() => void itemsQuery.refetch()}>
                Try again
              </button>
            </div>
          )}
        </div>
      </section>
    )
  }

  if (items.length === 0) {
    return (
      <section className="view on" id="v-scope">
        <div className="pg">
          {crumb}
          {header}
          <div id="sc-empty" className="card center">
            <h3 style={{ justifyContent: 'center' }}>No scope yet</h3>
            <p className="muted small">Run AI Project Intake first.</p>
            <button className="btn" type="button" onClick={() => goToStep('intake')}>
              Go to AI Intake
            </button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="view on" id="v-scope">
      <div className="pg">
        {crumb}
        {header}
        <PlanInForceBanner projectId={projectId} id="sc-plan-in-force" />
        {actionError && (
          <div className="cf-banner bad" role="alert">
            {errorMessage(actionError)}
          </div>
        )}
        <div id="sc-body">
          {SCOPE_CATEGORIES.map((category) => {
            const shown = visible.filter((item) => item.category === category)
            const acceptedInSection = shown.filter((item) => item.status === 'accepted').length
            return (
              <div className="card sc-sec" key={category}>
                <div className="sc-head">
                  <span className="nm">
                    {SCOPE_CATEGORY_LABELS[category]}{' '}
                    <span className="sc-badge" style={{ background: SECTION_COLOR[category] }}>
                      {acceptedInSection}/{shown.length} accepted
                    </span>
                  </span>
                  <button
                    className="btn sec sm"
                    type="button"
                    onClick={() => {
                      setAdding(category)
                      setAddText('')
                    }}
                  >
                    + Add item
                  </button>
                </div>
                {shown.map((item) => (
                  <ScopeItemRow key={item.id} {...rowProps(item)} />
                ))}
                {adding === category && (
                  <div className="item">
                    <span className="ic">+</span>
                    <div className="item-main">
                      <input
                        className="in"
                        value={addText}
                        onChange={(event) => setAddText(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') saveAdd(category)
                          if (event.key === 'Escape') setAdding(null)
                        }}
                        aria-label={`New ${SCOPE_CATEGORY_LABELS[category]} item`}
                        placeholder="Describe the item in your own words"
                        maxLength={2000}
                        autoFocus
                      />
                    </div>
                    <div className="item-side">
                      <Chip kind="yours" />
                      <div className="acts">
                        <button className="btn sm" type="button" onClick={() => saveAdd(category)} disabled={addItem.isPending || !addText.trim()}>
                          {addItem.isPending ? 'Adding…' : 'Add'}
                        </button>
                        <button className="btn sec sm" type="button" onClick={() => setAdding(null)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {removed.length > 0 && (
            <div className="card sc-sec" id="sc-removed">
              <div className="sc-head">
                <span className="nm">
                  Removed <span className="sc-badge">{removed.length}</span>
                </span>
                <span className="small muted">Undo puts an item back as pending.</span>
              </div>
              {removed.map((item) => (
                <ScopeItemRow key={item.id} {...rowProps(item)} categoryLabel={SCOPE_CATEGORY_LABELS[item.category]} />
              ))}
            </div>
          )}
        </div>
        <GateBanner
          id="sc-banner"
          open={tried > 0}
          focusKey={tried}
          lead={gate.lead}
          items={gate.links}
        />
        <div className="row between mt" id="sc-foot">
          <button className="btn sec" type="button" onClick={() => goToStep('intake')}>
            &larr; Back to AI Intake
          </button>
          <div className="row">
            <span className={`gate${gate.blocked ? ' bad' : ''}`} id="sc-gate">
              {gate.status}
            </span>
            <button className="btn" id="btn-to-wbs" type="button" onClick={continueToWbs}>
              Continue to WBS Builder &rarr;
            </button>
          </div>
        </div>
        {toast}
      </div>
    </section>
  )
}
