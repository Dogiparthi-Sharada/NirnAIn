import type { ReactNode } from 'react'

/** Building blocks of the Create Project sections (mockup styles/create-form.css). */

export function SectionHeadBody({ n, title, help, required = false }: { n: number; title: string; help: string; required?: boolean }) {
  return (
    <>
      <span className="cf-num" aria-hidden="true">
        {n}
      </span>
      <div>
        <div className="cf-ttl">{title}</div>
        <div className="help">{help}</div>
      </div>
      <span className={required ? 'cf-badge req' : 'cf-badge'}>{required ? 'Required' : 'Optional'}</span>
    </>
  )
}

export function SectionHead({ id, ...props }: { n: number; id: string; title: string; help: string; required?: boolean }) {
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
