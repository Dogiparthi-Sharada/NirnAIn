/**
 * The signed-in app frame (mockup index.html `#app`, scripts/core/router.js, styles/shell.css):
 * `aside#sidebar` with the brand, the workflow steps, the current project and the account menu;
 * `main#main` for the screen; the docked NirnAIn AI panel `aside#chat` (or the floating
 * `#chat-open` button when it is closed).
 *
 * Step locks follow the design: steps 2–6 need an open project (the project in the URL, or on the
 * dashboard the one picked there), step 7 needs an applied plan (`GET …/plan-versions` non-empty;
 * errors count as locked). Rendered by `App.tsx` around every signed-in route, inside
 * `OrgProvider`; sign-out goes through `useAuth().signOut`.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useAuth } from '@/auth/useAuth'
import { useOrg } from '@/org/useOrg'
import { useProject } from '@/lib/queries'
import { FLOW_STEPS, stepPath, type FlowStepKey } from '@/lib/steps'
import { initials } from '@/lib/format'
import { useCurrentProjectId, useLastProjectId, usePlanVersions, useScreenContext } from '@/lib/frame'
import { toast } from '@/lib/toast'
import AIChat from './AIChat'

const ROLE_LABELS: Record<string, string> = { owner: 'Owner', admin: 'Admin', pm: 'PM', member: 'Member', viewer: 'Viewer' }

/* ─────────────────────────── Assistant panel state ─────────────────────────── */

const CHAT_OPEN_KEY = 'nirnain.assistant.open'
const WIDE_SCREEN_QUERY = '(min-width: 1100px)'

function initialChatOpen(): boolean {
  try {
    const stored = window.localStorage.getItem(CHAT_OPEN_KEY)
    if (stored === 'true' || stored === 'false') return stored === 'true'
  } catch {
    // Storage can be unavailable (private mode, blocked cookies); fall back to the screen size.
  }
  return window.matchMedia(WIDE_SCREEN_QUERY).matches
}

function persistChatOpen(open: boolean) {
  try {
    window.localStorage.setItem(CHAT_OPEN_KEY, String(open))
  } catch {
    // Not persisting is harmless; the panel just uses the default next time.
  }
}

/* ─────────────────────────── Shell ─────────────────────────── */

/** Sidebar + screen + assistant; scrolls the screen to the top on every navigation. */
export default function Shell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const routeProjectId = useCurrentProjectId()
  const lastProjectId = useLastProjectId()
  // On the dashboard the picked project is the open one; elsewhere only the project in the URL.
  const projectId = routeProjectId ?? (pathname === '/dashboard' ? (lastProjectId ?? undefined) : undefined)
  const contextLabel = useScreenContext()
  const identity = useAccountIdentity()
  const mainRef = useRef<HTMLElement>(null)
  const openChatRef = useRef<HTMLButtonElement>(null)
  const chatInputRef = useRef<HTMLInputElement>(null)
  const [chatOpen, setChatOpen] = useState(initialChatOpen)
  const focusAfterToggle = useRef(false)

  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 })
  }, [pathname])

  useEffect(() => {
    if (!focusAfterToggle.current) return
    focusAfterToggle.current = false
    if (chatOpen) chatInputRef.current?.focus()
    else openChatRef.current?.focus()
  }, [chatOpen])

  function toggleChat(open: boolean) {
    focusAfterToggle.current = true
    setChatOpen(open)
    persistChatOpen(open)
  }

  return (
    <>
      <div id="app">
        <Sidebar projectId={projectId} identity={identity} />
        <main id="main" ref={mainRef}>
          {children}
        </main>
        <AIChat
          open={chatOpen}
          contextLabel={contextLabel}
          firstName={identity.firstName}
          projectId={routeProjectId}
          onClose={() => toggleChat(false)}
          inputRef={chatInputRef}
        />
      </div>
      {!chatOpen && (
        <button ref={openChatRef} className="btn" id="chat-open" type="button" onClick={() => toggleChat(true)}>
          &#10022; Ask NirnAIn AI
        </button>
      )}
    </>
  )
}

/* ─────────────────────────── Sidebar ─────────────────────────── */

/** Brand, workflow steps with their locks, the current project and the account menu. */
function Sidebar({ projectId, identity }: { projectId: string | undefined; identity: AccountIdentity }) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const project = useProject(projectId)
  const planVersions = usePlanVersions(projectId)
  const hasProject = Boolean(projectId && project.data)
  const hasPlan = (planVersions.data?.length ?? 0) > 0

  function activeStep(): FlowStepKey | 'dashboard' | null {
    if (pathname === '/dashboard') return 'dashboard'
    if (pathname.startsWith('/projects/new')) return 'create'
    const segment = /^\/projects\/[^/]+\/([^/]+)/.exec(pathname)?.[1]
    return FLOW_STEPS.find((step) => step.segment === segment)?.key ?? null
  }
  const active = activeStep()

  function lockReason(step: FlowStepKey): string | null {
    if (step === 'create') return null
    if (!hasProject) return 'Open or create a project first'
    if (step === 'changes' && !hasPlan) return 'Apply a plan on step 6 first'
    return null
  }

  return (
    <aside id="sidebar">
      <Link className="sb-brand" to="/dashboard" style={{ color: 'inherit', textDecoration: 'none' }}>
        <span className="bmark sm" aria-hidden="true">
          N
        </span>
        <span>
          <span className="bname" style={{ fontWeight: 700 }}>
            Nirn<em>AI</em>n
          </span>
          <small>Planning Platform</small>
        </span>
      </Link>
      <nav className="sb-nav" aria-label="Workflow">
        <div className="sb-label">Workflow</div>
        <button
          className={active === 'dashboard' ? 'nav on' : 'nav'}
          type="button"
          aria-current={active === 'dashboard' ? 'page' : undefined}
          onClick={() => navigate('/dashboard')}
        >
          <span className="nb dot" aria-hidden="true" />
          Dashboard
        </button>
        {hasProject && project.data && (
          <div className="sb-proj" id="sb-proj">
            <div className="sb-label" style={{ padding: 0, margin: 0 }}>
              Current project
            </div>
            <div className="mono small muted">{project.data.code}</div>
            <div className="pname">{project.data.name}</div>
          </div>
        )}
        {FLOW_STEPS.map((step, index) => {
          const reason = lockReason(step.key)
          return (
            <button
              key={step.key}
              className={active === step.key ? 'nav on' : 'nav'}
              type="button"
              disabled={reason !== null}
              title={reason ?? undefined}
              aria-current={active === step.key ? 'page' : undefined}
              onClick={() => navigate(stepPath(step.key, projectId))}
            >
              <span className="nb" aria-hidden="true">
                {index + 1}
              </span>
              {step.label}
              {reason && <span className="sr-only"> ({reason})</span>}
            </button>
          )
        })}
        {!hasProject && (
          <p className="sb-hint" id="sb-hint">
            Open or create a project first to unlock the planning steps.
          </p>
        )}
      </nav>
      <UserMenu identity={identity} />
    </aside>
  )
}

/* ─────────────────────────── Account identity ─────────────────────────── */

function metadataString(metadata: Record<string, unknown> | undefined, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = metadata?.[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

interface AccountIdentity {
  name: string
  firstName: string | null
  email: string
  roleLabel: string
}

/** Who is signed in: the profile first, then the Supabase user metadata, then the email. */
function useAccountIdentity(): AccountIdentity {
  const { user } = useAuth()
  const { me, org } = useOrg()
  const metadata = user?.user_metadata as Record<string, unknown> | undefined
  const email = me.email || user?.email || ''
  const fullName = me.fullName?.trim() || metadataString(metadata, 'full_name', 'name')
  return {
    name: fullName || email.split('@')[0] || 'Account',
    firstName: fullName?.split(/\s+/)[0] ?? null,
    email,
    roleLabel: ROLE_LABELS[org.role] ?? org.role,
  }
}

/* ─────────────────────────── User menu ─────────────────────────── */

/** The account button and its menu (name, email, workspace + role, Sign out); Escape or a click outside closes it. */
function UserMenu({ identity }: { identity: AccountIdentity }) {
  const { signOut } = useAuth()
  const { org } = useOrg()
  const [open, setOpen] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const signOutRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    signOutRef.current?.focus()
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  async function handleSignOut() {
    setSigningOut(true)
    try {
      await signOut()
      toast('Signed out.')
    } finally {
      setSigningOut(false)
    }
  }

  return (
    <div
      className="sb-user"
      ref={containerRef}
      onBlur={(event) => {
        if (open && !containerRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      {open && (
        <div className="user-menu" id="user-menu" role="menu" aria-label="Account">
          <div>
            <b>{identity.name}</b>
            <small>{identity.email}</small>
          </div>
          <div>
            {org.name} <span className="chip st-default">{identity.roleLabel}</span>
          </div>
          <button ref={signOutRef} id="btn-signout" type="button" role="menuitem" onClick={() => void handleSignOut()} disabled={signingOut}>
            &#x21AA; {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      )}
      <button
        ref={buttonRef}
        id="user-btn"
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? 'user-menu' : undefined}
        onClick={() => setOpen((previous) => !previous)}
      >
        <span className="avatar" aria-hidden="true">
          {initials(identity.name)}
        </span>
        <span>
          <b style={{ fontSize: 13 }}>{identity.name}</b>
          <small>
            {identity.roleLabel} &middot; {org.name}
          </small>
        </span>
      </button>
    </div>
  )
}
