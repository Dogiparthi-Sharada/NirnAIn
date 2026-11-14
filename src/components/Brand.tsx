/**
 * The NirnAIn brand: the app name, the "N" logo mark and the wordmark.
 *
 * Purpose: The brand appears on the sign-in screens, the workspace loading states and the shell,
 * and must look the same everywhere.
 * How it fits: Used by `auth/AuthLayout.tsx`, `org/OrgProvider.tsx`, `components/ErrorPages.tsx`
 * and page titles across the screens (`APP_NAME`); layer: component.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { cx } from '@/lib/classNames'

export const APP_NAME = 'NirnAIn'

/** Square "N" logo mark. `inverted` is for use on the primary-colored brand panel. */
export function BrandMark({ size = 32, inverted = false }: { size?: number; inverted?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className={cx(
        'flex shrink-0 items-center justify-center rounded-lg font-bold',
        inverted ? 'bg-white/15 text-white ring-1 ring-white/25' : 'bg-primary text-primary-foreground',
      )}
      style={{ width: size, height: size, fontSize: size * 0.44 }}
    >
      N
    </div>
  )
}

/** Wordmark: Nirn<AI>n with the "AI" highlighted. */
export function BrandName({ inverted = false, className }: { inverted?: boolean; className?: string }) {
  return (
    <span className={cx('font-bold tracking-[-0.01em]', className)}>
      Nirn<span className={inverted ? 'text-[#E8C9C4]' : 'text-primary'}>AI</span>n
    </span>
  )
}
