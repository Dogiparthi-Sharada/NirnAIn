/**
 * Building blocks shared by both Create Project paths (`screens/CreateProject.tsx`, the manual form,
 * and `screens/CreateFromUpload.tsx` + `components/DraftReviewForm.tsx`, the upload review), styled by
 * the approved design's `styles/create-form.css`: numbered section heads, labelled fields, the gap
 * banner and the "Required to continue" checklist.
 *
 * Design rule they implement: Continue / Create are never greyed out. A blocked click opens the gap
 * banner instead, listing each problem as a link that focuses its field, and moves focus to the
 * banner so screen readers announce it.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode, type Ref, type SelectHTMLAttributes } from 'react'

/** Number badge, title, help line and Required/Optional badge (also used inside a `<summary>`). */
export function SectionHeadBody({ number, title, help, required = false }: { number: number; title: string; help: string; required?: boolean }) {
  return (
    <>
      <span className="cf-num" aria-hidden="true">
        {number}
      </span>
      <div>
        <div className="cf-ttl">{title}</div>
        <div className="help">{help}</div>
      </div>
      <span className={required ? 'cf-badge req' : 'cf-badge'}>{required ? 'Required' : 'Optional'}</span>
    </>
  )
}

/** A section head as a block; `id` labels its section for assistive tech. */
export function SectionHead({ id, ...props }: { number: number; id: string; title: string; help: string; required?: boolean }) {
  return (
    <div className="cf-sec-head" id={id}>
      <SectionHeadBody {...props} />
    </div>
  )
}

/** A labelled control (`children`); `chip` sits in the label (provenance), `after` below the control (quotes, notes). */
export function Field({
  id,
  label,
  required,
  error,
  hint,
  chip,
  className = 'field',
  after,
  children,
}: {
  id: string
  label: string
  required?: boolean
  error?: string
  hint?: string
  chip?: ReactNode
  className?: string
  after?: ReactNode
  children: ReactNode
}) {
  return (
    <div className={className}>
      <label htmlFor={id}>
        {label}
        {required && (
          <span className="req" aria-hidden="true">
            *
          </span>
        )}
        {chip}
      </label>
      {children}
      {error ? (
        <p className="err" id={`${id}-err`}>
          {error}
        </p>
      ) : (
        hint && <p className="note">{hint}</p>
      )}
      {after}
    </div>
  )
}

/** One problem in the gap banner; `key` is handed back to `onJump`. */
export interface GapItem {
  key: string
  label: string
  /** Shown after the label ("Label: message"); omitted for a bare line. */
  message?: string
  /** False for a plain line with nothing to jump to (e.g. "and 3 more"). */
  link?: boolean
}

/**
 * The gap banner: amber (`warn`) as a heads-up, red (`bad`) after a blocked click. Hidden when there
 * is nothing to show; a server error, when given, is listed first.
 */
export function GapBanner({
  id,
  tone,
  lead,
  items,
  error,
  onJump,
  ref,
  className,
}: {
  id: string
  tone: 'warn' | 'bad'
  lead: string
  items: GapItem[]
  error?: { title: string; detail: string } | null
  onJump: (key: string) => void
  ref?: Ref<HTMLDivElement>
  /** Extra classes, e.g. `mt` when the banner sits below a card. */
  className?: string
}) {
  return (
    <div className={['cf-banner', tone, className].filter(Boolean).join(' ')} id={id} role="alert" tabIndex={-1} ref={ref} hidden={items.length === 0 && !error}>
      {error && (
        <>
          <b>&#9888; {error.title}.</b> {error.detail}
        </>
      )}
      {items.length > 0 && (
        <>
          {error && <br />}
          <b>&#9888; {lead}</b>
          <ul>
            {items.map((item) => (
              <li key={item.key}>
                {item.link === false ? (
                  item.label
                ) : (
                  <a
                    href="#"
                    data-jump={item.key}
                    onClick={(event) => {
                      event.preventDefault()
                      onJump(item.key)
                    }}
                  >
                    {item.label}
                  </a>
                )}
                {item.message && `: ${item.message}`}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/**
 * Focus for the gap banner. `request()` scrolls to and focuses it after the next render (the banner
 * is usually hidden until the state change that made it visible has rendered).
 */
export function useBannerFocus(): { ref: Ref<HTMLDivElement>; request: () => void } {
  const ref = useRef<HTMLDivElement>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (tick === 0) return
    ref.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    ref.current?.focus({ preventScroll: true })
  }, [tick])
  const request = useCallback(() => setTick((count) => count + 1), [])
  return { ref, request }
}

/** The "Required to continue" card: N of M, a progress meter and one row per required field. */
export function RequiredChecklist({
  items,
  onJump,
}: {
  items: { key: string; label: string; problem: string | undefined }[]
  onJump: (key: string) => void
}) {
  const done = items.filter((item) => !item.problem).length
  return (
    <div className="card cf-check">
      <div className="row between">
        <b>Required to continue</b>
        <span className="cf-count" id="cf-count">
          {done} of {items.length}
        </span>
      </div>
      <div className="cf-meter" aria-hidden="true">
        <i id="cf-meter" style={{ width: `${(done / items.length) * 100}%` }} />
      </div>
      <ul className="cf-list" id="cf-list">
        {items.map((item) =>
          item.problem ? (
            <li key={item.key} className="bad">
              <a
                href="#"
                data-jump={item.key}
                onClick={(event) => {
                  event.preventDefault()
                  onJump(item.key)
                }}
              >
                {item.label}
              </a>
              <small>{item.problem}</small>
            </li>
          ) : (
            <li key={item.key} className="ok">
              {item.label}
            </li>
          ),
        )}
      </ul>
    </div>
  )
}

/** The status chip next to the actions: red with what is left (`todo`), green "✓ {ready}" when nothing is. */
export function StatusChip({ id, todo, ready }: { id: string; todo: string | null; ready: string }) {
  return (
    <span className={`cf-status ${todo ? 'todo' : 'done'}`} id={id} aria-live="polite">
      {todo ?? `✓ ${ready}`}
    </span>
  )
}

/** A `<select>` over a fixed list of values (enum options with their display labels). */
export function OptionsSelect<T extends string>({
  options,
  labels,
  value,
  onValue,
  ...rest
}: {
  options: readonly T[]
  labels?: Record<T, string>
  value: T
  onValue: (value: T) => void
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'>) {
  return (
    <select {...rest} value={value} onChange={(event) => onValue(event.target.value as T)}>
      {options.map((option) => (
        <option key={option} value={option}>
          {labels ? labels[option] : option}
        </option>
      ))}
    </select>
  )
}
