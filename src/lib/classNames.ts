/**
 * The `cx` helper that joins conditional class names.
 *
 * Purpose: Components build `className` strings from optional parts; one helper keeps that
 * consistent instead of each file carrying its own copy.
 * How it fits: Used by `components/ui.tsx` and `components/Brand.tsx`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

/** Joins the truthy class names into one `className` string. */
export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(' ')
}
