/**
 * Scope Review's gate to the WBS (step 3 → step 4): what still stops "Continue to WBS Builder".
 *
 * Business rules: the WBS is built from the accepted scope, so every item must be decided
 * (accepted or removed) before the PM moves on (product owner, round 2: pending items block,
 * not only flagged ones). An item that says more than the notes (evidence 'flagged') must
 * also be resolved: use the quoted version, remove it, or accept it as the PM's own statement.
 * Used by `ScopeReview.tsx`: the gate text next to Continue and the red banner a blocked click
 * opens, whose links focus each pending item and offer the screen's "accept all quoted".
 */
import type { GateItem } from '@/components/InputsKit'
import type { EvidenceScopeItem } from '@/lib/inputs'

// The banner names at most this many items, then says how many more are pending.
const LISTED_ITEMS = 10

/** An item's text shortened for a gate link. */
const short = (text: string) => (text.length > 70 ? `${text.slice(0, 67)}\u2026` : text)

export interface ScopeGate {
  blocked: boolean
  /** The status next to Continue. */
  status: string
  /** The banner's lead sentence and its links. */
  lead: string
  links: GateItem[]
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many
}

/** What blocks Continue; `acceptQuoted` is the one-click bulk accept of the items quoted from the notes. */
export function scopeGate(items: EvidenceScopeItem[], acceptQuoted: () => void): ScopeGate {
  const pending = items.filter((item) => item.status === 'pending')
  const flagged = pending.filter((item) => item.evidence === 'flagged')
  const others = pending.filter((item) => item.evidence !== 'flagged')
  const quoted = others.filter((item) => item.evidence === 'stated').length

  let status = 'Every item is accepted or removed.'
  if (flagged.length) {
    status = `${flagged.length} ${plural(flagged.length, 'item says', 'items say')} more than your notes. Fix, remove or accept ${plural(flagged.length, 'it', 'them')} to continue.`
    if (others.length) status += ` ${others.length} more ${plural(others.length, 'is', 'are')} still pending.`
  } else if (pending.length) {
    status = `${pending.length} ${plural(pending.length, 'item is', 'items are')} still pending. Accept or remove ${plural(pending.length, 'it', 'them')} to continue.`
  }

  let lead = `${pending.length} ${plural(pending.length, 'item is', 'items are')} still pending. Accept or remove each one before you continue:`
  if (flagged.length && !others.length) {
    lead = `${flagged.length === 1 ? '1 item says' : `${flagged.length} items say`} more than your notes. Use the quoted version, remove it, or accept it as your own statement before you continue:`
  } else if (flagged.length) {
    lead = `${pending.length} items are still pending. Accept or remove each one before you continue. The first ${flagged.length === 1 ? 'one says' : `${flagged.length} say`} more than your notes: use the quoted version, remove it, or accept it as your own statement:`
  }

  const links: GateItem[] = [...flagged, ...others].slice(0, LISTED_ITEMS).map((item) => ({
    label: `\u201C${short(item.text)}\u201D`,
    target: `[data-id="${item.id}"]`,
  }))
  if (pending.length > LISTED_ITEMS) links.push({ label: `\u2026and ${pending.length - LISTED_ITEMS} more pending` })
  if (quoted) links.push({ label: `Accept all ${quoted} ${plural(quoted, 'item', 'items')} quoted from your notes`, onClick: acceptQuoted })
  return { blocked: pending.length > 0, status, lead, links }
}
