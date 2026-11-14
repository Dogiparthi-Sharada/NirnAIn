/**
 * Immutable Set helpers for React state: toggle a value, drop a value.
 *
 * Purpose: State updates need a new Set; one helper replaces the copy-then-toggle blocks the
 * pickers, collapsible rows and leaver lists each carried.
 * How it fits: Used by `lib/wbs.ts`, `screens/WBSBuilder.tsx`,
 * `screens/constraints/MemberForm.tsx` and `screens/changes/*`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

/** A copy of `set` with `value` removed when it was there, else added (state updates need a new Set). */
export function toggledIn<TValue>(set: ReadonlySet<TValue>, value: TValue): Set<TValue> {
  const next = new Set(set)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

/** A copy of `set` without `value`. */
export function withoutValue<TValue>(set: ReadonlySet<TValue>, value: TValue): Set<TValue> {
  const next = new Set(set)
  next.delete(value)
  return next
}
