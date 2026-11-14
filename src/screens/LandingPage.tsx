/**
 * Public landing page at `/` (mockup views/landing.js): header with section links, hero with a
 * sample work breakdown, about, features, how it works, call to action and footer.
 *
 * Static marketing copy, no data. Signed-in visitors (Supabase session from `useAuth`) see
 * "Go to Dashboard" in place of Sign In / Get Started.
 */
import { useEffect, type MouseEvent } from 'react'
import { Link } from 'react-router'
import { useAuth } from '@/auth/useAuth'
import { BrandLockup } from '@/auth/AuthLayout'
import { APP_NAME } from '@/components/Brand'

const SECTIONS = [
  { id: 'lp-about', label: 'About' },
  { id: 'lp-features', label: 'Features' },
  { id: 'lp-how', label: 'How It Works' },
] as const

const FEATURES = [
  { tag: 'AI', title: 'AI Project Intake', text: 'Drop in meeting transcripts, PDFs, Word files and audio. AI extracts goals, scope, stakeholders and assumptions for your review.', background: '#E8DCC6' },
  { tag: 'WBS', title: 'WBS / PBS / OBS / RACI', text: 'Generate linked work, product and organizational breakdown structures, with responsibility matrices built from the same model.', background: '#DDE7D0' },
  { tag: 'RSK', title: 'Risk Management', text: 'Surface risks from your scope and constraints, rate impact and likelihood, and tie mitigations to the work packages they affect.', background: '#F3D9D5' },
  { tag: 'OPT', title: 'Optimization Engine', text: 'Solve resource allocation and scheduling against budget, deadline, capacity and skill constraints with OR-Tools, and compare feasible scenarios.', background: '#F5E3C8' },
  { tag: 'TRC', title: 'Traceability', text: 'Follow every task back to the requirement, meeting or document it came from, and see what changes when a source changes.', background: '#E3DCCB' },
  { tag: 'GOV', title: 'AI Governance', text: 'AI proposes, people decide. Every AI suggestion is logged, reviewable and requires explicit human approval before it enters the plan.', background: '#F3EADB' },
] as const

const STEPS = [
  { title: 'Project Information', text: 'Meetings, documents and requirements' },
  { title: 'AI Analysis', text: 'Scope, deliverables and gaps extracted' },
  { title: 'Structured Project Model', text: 'WBS, PBS, OBS and RACI' },
  { title: 'Constraints & Risk', text: 'Hard and soft limits, risk register' },
  { title: 'Optimization', text: 'OR-Tools scheduling and allocation' },
  { title: 'Human Approval', text: 'Review, adjust and sign off' },
  { title: 'Execution & Monitoring', text: 'Track progress against the plan' },
] as const

/** Section links scroll smoothly instead of changing the URL hash (the router owns the URL). */
function scrollToSection(event: MouseEvent<HTMLAnchorElement>, id: string) {
  event.preventDefault()
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** The marketing page; every call to action goes to sign-up, sign-in or (signed in) the dashboard. */
export default function LandingPage() {
  const { session } = useAuth()
  const signedIn = Boolean(session)

  useEffect(() => {
    document.title = `${APP_NAME} — AI Project Planning`
  }, [])

  return (
    <section className="view on" id="v-landing">
      <header className="lp-head">
        <Link to="/" aria-label={`${APP_NAME} home`} style={{ color: 'inherit', textDecoration: 'none' }}>
          <BrandLockup />
        </Link>
        <nav aria-label="Sections">
          {SECTIONS.map((section) => (
            <a key={section.id} href={`#${section.id}`} onClick={(event) => scrollToSection(event, section.id)}>
              {section.label}
            </a>
          ))}
        </nav>
        <div className="row">
          {signedIn ? (
            <Link className="btn" to="/dashboard">
              Go to Dashboard
            </Link>
          ) : (
            <>
              <Link className="btn ghost" to="/login">
                Sign In
              </Link>
              <Link className="btn" to="/signup">
                Get Started
              </Link>
            </>
          )}
        </div>
      </header>

      <main>
        <div className="lp-hero">
          <div>
            <span className="eyebrow">AI-Assisted Project Planning Platform</span>
            <h1>
              Turn Project Ideas Into <span className="grad">Structured, Optimized Plans</span>
            </h1>
            <p>
              NirnAIn uses AI to transform meetings, documents and requirements into structured project plans, then applies
              optimization for resource allocation, scheduling and decision support.
            </p>
            <div className="row mt">
              {signedIn ? (
                <Link className="btn lg" to="/dashboard">
                  Go to Dashboard
                </Link>
              ) : (
                <>
                  <Link className="btn lg" to="/signup">
                    Get Started
                  </Link>
                  <Link className="btn ghost lg" to="/login">
                    Sign In
                  </Link>
                </>
              )}
            </div>
          </div>
          <HeroTree />
        </div>

        <div className="lp-white">
          <div className="lp-sec lp-about" id="lp-about">
            <h2>Planning shouldn&apos;t start from a blank page.</h2>
            <p style={{ fontSize: 16, lineHeight: 1.7, color: '#6B5254', margin: 0 }}>
              Project knowledge is scattered across calls, documents and inboxes. NirnAIn gathers it, builds a structured and
              traceable project model, and uses mathematical optimization to test what is actually feasible. AI does the heavy
              lifting, and your team keeps the final say.
            </p>
          </div>
        </div>

        <div className="lp-sec" id="lp-features">
          <h2 style={{ fontSize: 26 }}>Features</h2>
          <div className="lp-feats">
            {FEATURES.map((feature) => (
              <div key={feature.tag} className="card">
                <span className="ft" style={{ background: feature.background }}>
                  {feature.tag}
                </span>
                <h3>{feature.title}</h3>
                <p className="small muted">{feature.text}</p>
              </div>
            ))}
          </div>
        </div>

        <div style={{ background: 'var(--sec)' }}>
          <div className="lp-sec" id="lp-how">
            <h2 style={{ fontSize: 26 }}>How It Works</h2>
            <div className="lp-steps">
              {STEPS.map((step, index) => (
                <div key={step.title}>
                  <span className="n">{index + 1}</span>
                  <b>{step.title}</b>
                  {step.text}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="lp-cta">
          <h2>Ready to structure your next project?</h2>
          <p>Create a workspace and turn your first meeting notes into a plan.</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            {signedIn ? (
              <Link className="btn lg" style={{ background: '#fff', color: 'var(--pr)' }} to="/dashboard">
                Go to Dashboard
              </Link>
            ) : (
              <>
                <Link className="btn lg" style={{ background: '#fff', color: 'var(--pr)' }} to="/signup">
                  Create Account
                </Link>
                <Link className="btn ghost lg" style={{ color: '#fff', borderColor: '#fff' }} to="/login">
                  Sign In
                </Link>
              </>
            )}
          </div>
        </div>
      </main>

      <footer className="lp-foot">
        &copy; {new Date().getFullYear()} {APP_NAME} Inc.
      </footer>
    </section>
  )
}

/** The hero's sample work breakdown: a root, three deliverables and three work packages (one AI-suggested). */
function HeroTree() {
  return (
    <div className="card lp-tree" aria-label="Example work breakdown structure" role="img">
      <div className="tn root">1.0 Enterprise Data Platform</div>
      <div className="tbranch">
        <div className="tn">1.1 Data Ingestion Layer</div>
        <div className="tn">1.2 Data Warehouse &amp; ETL</div>
        <div className="tn">1.3 Dashboard &amp; Reporting</div>
      </div>
      <div className="tleaves">
        <div className="tn leaf">1.1.1 Salesforce connector</div>
        <div className="tn leaf">1.2.2 ETL pipeline</div>
        <div className="tn leaf ai">&#10022; AI suggested: 1.3.4 UAT</div>
      </div>
      <span className="fchip">
        <span className="pulse" />
        OR-Tools: optimal schedule in 0.4s
      </span>
      <span className="fchip">
        <span className="pulse" />
        RACI linked to 6 work packages
      </span>
    </div>
  )
}
