import { useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { Chip, Crumb, GateBanner, useToast } from '@/components/InputsKit'
import { errorMessage } from '@/lib/api'
import type { EvidenceScopeItem } from '@/lib/inputs'
import { useAddScopeItem, useLatestIntakeRun, useScopeItems, useUpdateScopeItem } from '@/lib/queries'
import { useStepNav } from '@/lib/steps'
import { SCOPE_CATEGORIES, SCOPE_CATEGORY_LABELS, type ScopeCategory, type ScopeItemPatch } from '@/lib/types'
import { PlanInForceBanner } from './changes/InForceBanner'
import { scopeGate } from './scope/gate'

const SECTION_COLOR: Record<ScopeCategory, string> = {
  objective: '#DDE7D0',
  requirement: '#E3DCCB',
  deliverable: '#F5E3C8',
  constraint: '#F3D9D5',
  risk: '#F5E3C8',
  missing_info: '#F3EADB',
}

export default function ScopeReview() {
  const nav = useStepNav()
  const { projectId = '' } = useParams()
  const itemsQuery = useScopeItems(projectId)
  const intake = useLatestIntakeRun(projectId)
  const updateItem = useUpdateScopeItem(projectId)
  const addItem = useAddScopeItem(projectId)
  const [toast, showToast] = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [adding, setAdding] = useState<ScopeCategory | null>(null)
  const [addText, setAddText] = useState('')
  const [tried, setTried] = useState(0)

  const items = useMemo(() => (itemsQuery.data ?? []) as EvidenceScopeItem[], [itemsQuery.data])
  const visible = items.filter((i) => i.status !== 'removed')
  const accepted = visible.filter((i) => i.status === 'accepted').length
  // Pending items (and, first, those saying more than the notes) block the WBS step.
  const gate = scopeGate(items, () => void acceptAllQuoted())
  const actionError = updateItem.error ?? addItem.error

  function patch(itemId: string, body: ScopeItemPatch) {
    updateItem.mutate({ itemId, patch: body })
  }

  function saveEdit(item: EvidenceScopeItem) {
    const text = editText.trim()
    if (text && text !== item.text) patch(item.id, { text })
    setEditing(null)
  }

  function saveAdd(category: ScopeCategory) {
    const text = addText.trim()
    if (!text) return
    addItem.mutate({ category, text }, { onSuccess: () => setAdding(null) })
  }

  async function acceptAllQuoted() {
    const quoted = items.filter((i) => i.evidence === 'stated' && i.status === 'pending')
    const results = await Promise.allSettled(
      quoted.map((i) => updateItem.mutateAsync({ itemId: i.id, patch: { status: 'accepted' } })),
    )
    const done = results.filter((r) => r.status === 'fulfilled').length
    const left = items.filter((i) => i.status === 'pending').length - done
    showToast(`Accepted ${done} items quoted from your notes. ${left} left for you to check.`)
  }

  function continueToWbs() {
    if (gate.blocked) setTried((attempts) => attempts + 1)
    else nav('wbs')
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
            <button className="btn" type="button" onClick={() => nav('intake')}>
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
            const all = items.filter((i) => i.category === category)
            const shown = all.filter((i) => i.status !== 'removed')
            const acc = shown.filter((i) => i.status === 'accepted').length
            return (
              <div className="card sc-sec" key={category}>
                <div className="sc-head">
                  <span className="nm">
                    {SCOPE_CATEGORY_LABELS[category]}{' '}
                    <span className="sc-badge" style={{ background: SECTION_COLOR[category] }}>
                      {acc}/{shown.length} accepted
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
                {all.map((item) => (
                  <ScopeItemRow
                    key={item.id}
                    item={item}
                    editing={editing === item.id}
                    editText={editText}
                    onEditText={setEditText}
                    onAccept={() => patch(item.id, { status: item.status === 'accepted' ? 'pending' : 'accepted' })}
                    onRemove={() => patch(item.id, { status: item.status === 'removed' ? 'pending' : 'removed' })}
                    onFix={() => item.suggestedText && patch(item.id, { text: item.suggestedText })}
                    onEdit={() => {
                      if (editing === item.id) saveEdit(item)
                      else {
                        setEditing(item.id)
                        setEditText(item.text)
                      }
                    }}
                    onCancelEdit={() => setEditing(null)}
                  />
                ))}
                {adding === category && (
                  <div className="item">
                    <span className="ic">+</span>
                    <div className="item-main">
                      <input
                        className="in"
                        value={addText}
                        onChange={(e) => setAddText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') saveAdd(category)
                          if (e.key === 'Escape') setAdding(null)
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
        </div>
        <GateBanner
          id="sc-banner"
          open={tried > 0}
          focusKey={tried}
          lead={gate.lead}
          items={gate.links}
        />
        <div className="row between mt" id="sc-foot">
          <button className="btn sec" type="button" onClick={() => nav('intake')}>
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

function ScopeItemRow({
  item,
  editing,
  editText,
  onEditText,
  onAccept,
  onRemove,
  onFix,
  onEdit,
  onCancelEdit,
}: {
  item: EvidenceScopeItem
  editing: boolean
  editText: string
  onEditText: (text: string) => void
  onAccept: () => void
  onRemove: () => void
  onFix: () => void
  onEdit: () => void
  onCancelEdit: () => void
}) {
  const acc = item.status === 'accepted'
  const rem = item.status === 'removed'
  const quote = item.sourceQuote
  let body = null
  if (item.evidence === 'stated' && quote) body = <div className="quote">&ldquo;{quote}&rdquo;</div>
  if (item.evidence === 'inferred') {
    body = (
      <>
        {quote && <div className="quote inf">Based on: &ldquo;{quote}&rdquo;</div>}
        {item.evidenceNote && <div className="note">{item.evidenceNote}</div>}
      </>
    )
  }
  if (item.evidence === 'flagged') {
    body = (
      <>
        {quote && <div className="quote">&ldquo;{quote}&rdquo;</div>}
        <div className="note bad">
          {item.evidenceNote ? <>Not in your notes: &ldquo;{item.evidenceNote}&rdquo;</> : 'Says more than your notes.'}
        </div>
      </>
    )
  }

  let chip = null
  if (item.origin === 'manual') chip = <Chip kind="yours" />
  else if (item.evidence === 'flagged') chip = acc ? <Chip kind="confirmed">&#10003; Your statement</Chip> : <Chip kind="flag" />
  else if (item.evidence === 'stated') chip = <Chip kind="stated" />
  else if (item.evidence === 'inferred') chip = <Chip kind="inferred" />

  return (
    <div className={`item${rem ? ' removed' : ''}${acc ? ' accepted' : ''}`} data-id={item.id} tabIndex={-1}>
      <span className="ic" aria-hidden="true">
        {acc ? '\u2713' : '\u25CB'}
      </span>
      <div className="item-main">
        {editing ? (
          <textarea
            className="in item-text"
            value={editText}
            onChange={(e) => onEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onEdit()
              }
              if (e.key === 'Escape') onCancelEdit()
            }}
            aria-label="Item text"
            maxLength={2000}
            rows={2}
            autoFocus
          />
        ) : (
          <div className="item-text">{item.text}</div>
        )}
        {body}
      </div>
      <div className="item-side">
        {chip}
        <div className="acts">
          {rem ? (
            <button className="btn sec sm" type="button" onClick={onRemove}>
              Undo
            </button>
          ) : (
            <>
              {item.evidence === 'flagged' && !acc && item.suggestedText && (
                <button className="btn sm" data-act="fix" type="button" onClick={onFix}>
                  Use the quoted version
                </button>
              )}
              <button className="btn sec sm" type="button" onClick={onAccept}>
                {acc ? 'Undo accept' : 'Accept'}
              </button>
              <button className="btn sec sm" type="button" onClick={onEdit}>
                {editing ? 'Done' : 'Edit'}
              </button>
              <button className="btn sec sm" type="button" onClick={onRemove}>
                Remove
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
