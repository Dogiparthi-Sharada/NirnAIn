/**
 * Shared UI primitives: spinners, the button and the error state.
 *
 * Purpose: Every data view needs the same loading and error states and an accessible button with
 * visible focus and a busy state that doesn't shift the layout.
 * How it fits: Used by the auth guards, `org/OrgProvider.tsx` and screens; layer: component.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import type { ButtonHTMLAttributes } from 'react'
import { cx } from '@/lib/classNames'

/* ─────────────────────────── Spinner ─────────────────────────── */

/** A spinning circle; decorative (hidden from screen readers). */
export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={cx('animate-spin', className)}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

/** A centred spinner with a label, announced politely, for whole-page loading. */
export function FullPageSpinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background text-muted-foreground">
      <Spinner size={22} className="text-primary" />
      <span className="text-[13px]">{label}…</span>
    </div>
  )
}

/* ─────────────────────────── Button ─────────────────────────── */

type ButtonVariant = 'primary' | 'secondary' | 'ghost'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-white border border-primary hover:bg-[#5E262D] disabled:bg-[#A86A70] disabled:border-[#A86A70]',
  secondary: 'bg-white text-primary border border-border hover:bg-[#FAF5EB] hover:border-[#B7A89A] disabled:text-[#A99A8E]',
  ghost: 'bg-transparent text-primary border border-transparent hover:bg-muted disabled:text-[#A99A8E]',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  loading?: boolean
  block?: boolean
  size?: 'sm' | 'md' | 'lg'
}

/**
 * A button in the primary, secondary or ghost style that shows a spinner while `loading` without changing its
 * width.
 */
export function Button({
  variant = 'primary',
  loading = false,
  block = false,
  size = 'md',
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  const sizes = { sm: 'px-3 py-1.5 text-xs', md: 'px-4 py-[9px] text-[13px]', lg: 'px-4 py-[11px] text-sm' }
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        'relative inline-flex items-center justify-center gap-2 rounded-[4px] font-semibold transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        'disabled:cursor-not-allowed cursor-pointer',
        BUTTON_VARIANTS[variant],
        sizes[size],
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {/* Content keeps its width while loading so nothing shifts. */}
      <span className={cx('inline-flex items-center justify-center gap-2', loading && 'invisible')}>{children}</span>
      {loading && (
        <span className="absolute inset-0 flex items-center justify-center">
          <Spinner size={16} />
        </span>
      )}
    </button>
  )
}

/* ─────────────────────────── Data states ─────────────────────────── */

/** A load-failure message with an optional Try again button. */
export function ErrorState({
  title = "We couldn't load this",
  message,
  onRetry,
  retrying,
  compact,
}: {
  title?: string
  message: string
  onRetry?: () => void
  retrying?: boolean
  compact?: boolean
}) {
  return (
    <div role="alert" className={cx('flex flex-col items-center text-center', compact ? 'py-6' : 'py-12')}>
      <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-full bg-[#F3D9D5] text-[#A83A4A]">
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M12 8v5M12 16h.01" />
          <circle cx="12" cy="12" r="9" />
        </svg>
      </div>
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <div className="mt-1 max-w-sm text-[13px] text-muted-foreground">{message}</div>
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry} loading={retrying}>
          Try again
        </Button>
      )}
    </div>
  )
}
