/**
 * The signed-in app frame: sidebar with the workflow steps, the screen and the docked AI panel.
 *
 * Purpose: Step locks follow the design: steps 2–6 need an open project (the one in the URL, or on
 * the dashboard the one picked there) and step 7 needs an applied plan (errors count as locked).
 * The assistant panel's open state is remembered, and the screen scrolls to the top on every
 * navigation.
 * How it fits: Port of the mockup's index.html `#app`, scripts/core/router.js and
 * styles/shell.css; rendered by `App.tsx` around every signed-in route inside `OrgProvider`;
 * sign-out goes through `useAuth().signOut`; layer: component.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useAuth } from '@/auth/useAuth'
import { useOrg } from '@/org/useOrg'
import { useProject } from '@/lib/queries'
import { FLOW_STEPS, stepPath, type FlowStepKey } from '@/lib/steps'
import { initials } from '@/lib/format'
import { rememberLastProjectId, useCurrentProjectId, useLastProjectId, usePlanVersions, useScreenContext } from '@/lib/frame'
import { toast } from '@/lib/toast'
import AIChat from './AIChat'

const ROLE_LABELS: Record<string, string> = { owner: 'Owner', admin: 'Admin', pm: 'PM', member: 'Member', viewer: 'Viewer' }

/* ─────────────────────────── Assistant panel state ─────────────────────────── */

const CHAT_OPEN_KEY = 'nirnain.assistant.open'
const WIDE_SCREEN_QUERY = '(min-width: 1100px)'

/** Whether the assistant panel starts open: the remembered choice, else open on wide screens. */
function initialChatOpen(): boolean {
  try {
    const stored = window.localStorage.getItem(CHAT_OPEN_KEY)
    if (stored === 'true' || stored === 'false') return stored === 'true'
  } catch {
    // Storage can be unavailable (private mode, blocked cookies); fall back to the screen size.
  }
  return window.matchMedia(WIDE_SCREEN_QUERY).matches
}

/** Remembers whether the assistant panel is open. */
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

  // The project the PM works on becomes the dashboard's default.
  useEffect(() => {
    if (routeProjectId) rememberLastProjectId(routeProjectId)
  }, [routeProjectId])

  useEffect(() => {
    if (!focusAfterToggle.current) return
    focusAfterToggle.current = false
    if (chatOpen) chatInputRef.current?.focus()
    else openChatRef.current?.focus()
  }, [chatOpen])

  /** Opens or closes the assistant panel, remembers it and moves focus to match. */
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

  /** The sidebar entry for the current URL (dashboard, a flow step or none). */
  function activeStep(): FlowStepKey | 'dashboard' | null {
    if (pathname === '/dashboard') return 'dashboard'
    if (pathname.startsWith('/projects/new')) return 'create'
    const segment = /^\/projects\/[^/]+\/([^/]+)/.exec(pathname)?.[1]
    return FLOW_STEPS.find((step) => step.segment === segment)?.key ?? null
  }
  const active = activeStep()

  /** Why a flow step can't be opened yet, or null when it can. */
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

/** The first non-empty string among `keys` in the Supabase user metadata. */
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
    /** Closes the menu on a click outside it. */
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    /** Closes the menu on Escape and returns focus to its button. */
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

  /** Signs out and confirms it in a toast. */
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
