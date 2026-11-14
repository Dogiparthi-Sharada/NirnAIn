/**
 * Error pages in the approved design's style: page not found, a crashed page and missing configuration.
 *
 * Purpose: Users must always get a way back instead of a blank screen. Inside the signed-in shell
 * (`inline`) the message sits in the page area; otherwise it fills the window with the brand above
 * it. The crash page uses plain anchors because it can render outside the router.
 * How it fits: `App.tsx` uses `RouteError` as the router's `errorElement` and `NotFound` for
 * unknown URLs; `main.tsx` shows `CrashPage` for an error no route caught and `ConfigErrorPage`
 * when env vars are missing; layer: component.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, type ReactNode } from 'react'
import { isRouteErrorResponse, Link, useRouteError } from 'react-router'
import { APP_NAME } from './Brand'

const DASHBOARD_PATH = '/dashboard'

/** A centred message card with optional status code and actions; `inline` keeps it inside the shell. */
export function MessagePage({
  code,
  title,
  message,
  actions,
  inline = false,
}: {
  code?: string
  title: string
  message: ReactNode
  actions?: ReactNode
  inline?: boolean
}) {
  const card = (
    <div className="card" style={{ width: '100%', maxWidth: 480, padding: '32px 32px 28px', textAlign: 'center' }}>
      {code && (
        <div className="mono small muted" style={{ letterSpacing: '.2em', marginBottom: 10 }}>
          {code}
        </div>
      )}
      <h1 style={{ justifyContent: 'center' }}>{title}</h1>
      <div className="sub" style={{ marginTop: 8, lineHeight: 1.6 }}>
        {message}
      </div>
      {actions && (
        <div className="row" style={{ justifyContent: 'center', marginTop: 24 }}>
          {actions}
        </div>
      )}
    </div>
  )

  if (inline) {
    return (
      <section className="view on" id="v-error">
        <div className="pg" style={{ display: 'flex', justifyContent: 'center', paddingTop: 72 }}>
          {card}
        </div>
      </section>
    )
  }
  return (
    <main
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 20,
        padding: '64px 24px',
        background: 'var(--bg)',
      }}
    >
      <div className="brand">
        <span className="bmark" aria-hidden="true">
          N
        </span>
        <span className="bname">
          Nirn<em>AI</em>n
        </span>
      </div>
      {card}
    </main>
  )
}

/** "Page not found", with a way back to the dashboard. */
export function NotFound({ inline = false }: { inline?: boolean }) {
  useEffect(() => {
    document.title = `Page not found · ${APP_NAME}`
  }, [])
  return (
    <MessagePage
      inline={inline}
      code="404"
      title="We can't find that page"
      message="The link may be broken, or the page may have moved. Check the address, or head back to your dashboard and pick up where you left off."
      actions={
        <Link to={DASHBOARD_PATH} className="btn">
          Back to Dashboard
        </Link>
      }
    />
  )
}

/** Router `errorElement`: renders 404s and unexpected render/loader errors. */
export function RouteError({ inline = false }: { inline?: boolean }) {
  const error = useRouteError()
  useEffect(() => {
    if (!(isRouteErrorResponse(error) && error.status === 404)) console.error(error)
  }, [error])

  if (isRouteErrorResponse(error) && error.status === 404) return <NotFound inline={inline} />
  return <CrashPage inline={inline} />
}

/** An unexpected error: reload, or start again from the dashboard. Your saved work is not affected. */
export function CrashPage({ inline }: { inline?: boolean }) {
  return (
    <MessagePage
      inline={inline}
      title="Something went wrong"
      message="An unexpected error interrupted this page. Your saved work is safe; reloading usually fixes it. If it keeps happening, please contact support."
      actions={
        <>
          <button type="button" className="btn sec" onClick={() => window.location.reload()}>
            Reload page
          </button>
          <a href={DASHBOARD_PATH} className="btn">
            Back to Dashboard
          </a>
        </>
      }
    />
  )
}

/** Shown instead of the app when required build-time env vars are missing. */
export function ConfigErrorPage({ problems }: { problems: { name: string; message: string }[] }) {
  return (
    <MessagePage
      title={`${APP_NAME} isn't configured`}
      message={
        <>
          <p style={{ margin: 0 }}>This build is missing required configuration. Set the following variables and rebuild:</p>
          <ul
            className="small"
            style={{ margin: '14px 0 0', padding: '10px 16px', listStyle: 'none', textAlign: 'left', background: 'var(--soft)', border: '1px solid var(--line)', borderRadius: 6 }}
          >
            {problems.map((problem) => (
              <li key={problem.name} style={{ padding: '3px 0' }}>
                <code className="mono" style={{ color: 'var(--fg)', fontWeight: 500 }}>
                  {problem.name}
                </code>{' '}
                <span>{problem.message}</span>
              </li>
            ))}
          </ul>
          <p className="small" style={{ margin: '14px 0 0' }}>
            See .env.example in the repository for the full list.
          </p>
        </>
      }
    />
  )
}
