/**
 * Dashboard (`/dashboard`): one project at a time, picked from the organization's real projects.
 *
 * Purpose: The cards describe the chosen project from its overview and "Next step" opens its first
 * unfinished step. The project shown is `?project=` (bookmarkable), else the one a screen sent the
 * PM here with, else the last opened in this browser, else the most recently updated; a one-off
 * notice (e.g. "Draft … saved.") shows in `#d-notice`.
 * How it fits: Port of the mockup's scripts/views/dashboard.js; route in `App.tsx`; reads
 * `useProjects` and `GET /projects/{projectId}/overview`; renders `components/DashboardCards.tsx`;
 * layer: screen.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useMemo } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { AiFindings, DashboardKpis, DeliveryHealth, OpenRisks, ProjectNotifications } from '@/components/DashboardCards'
import { errorMessage } from '@/lib/api'
import { rememberLastProjectId, stateString, useLastProjectId } from '@/lib/frame'
import { formatDate } from '@/lib/format'
import { useProjectOverview, type ProjectOverview } from '@/lib/overview'
import { useProjects } from '@/lib/queries'
import { stepPath } from '@/lib/steps'
import { STATUS_LABELS, type Project, type ProjectStatus } from '@/lib/types'
import { useOrg } from '@/org/useOrg'

/** Status dot colours from the design (mockup data/portfolio.js `STATUS`). */
const STATUS_COLORS: Record<ProjectStatus, string> = {
  draft: '#8F7B74',
  planning: '#4455aa',
  on_track: '#4F7A4A',
  at_risk: '#a8691f',
  delayed: '#A83A4A',
  completed: '#4F7A4A',
  archived: '#8F7B74',
}
/** The API's page size limit; enough for the picker. */
const PICKER_LIMIT = 100
const NOTICE_MS = 6000
const PROJECT_PARAM = 'project'

/** Most recently updated first: the project the PM touched last is the natural default. */
function byRecentUpdate(first: Project, second: Project): number {
  return Date.parse(second.updatedAt) - Date.parse(first.updatedAt)
}

/** The per-project dashboard with its picker, cards and next step. */
export default function Dashboard() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const lastProjectId = useLastProjectId()
  const { me, org } = useOrg()
  const projectsQuery = useProjects(org.id, { limit: PICKER_LIMIT })
  const firstPage = projectsQuery.data?.pages[0]
  const projects = useMemo(() => [...(firstPage?.items ?? [])].sort(byRecentUpdate), [firstPage])
  const requestedId = searchParams.get(PROJECT_PARAM) ?? stateString(location.state, 'projectId') ?? lastProjectId
  // An unknown or no-longer-visible project falls back to the most recently updated one.
  const selected = projects.find((project) => project.id === requestedId) ?? projects[0] ?? null
  const overview = useProjectOverview(selected?.id ?? null)
  const notice = stateString(location.state, 'notice')

  useEffect(() => {
    document.title = 'Dashboard · NirnAIn'
  }, [])

  useEffect(() => {
    if (selected) rememberLastProjectId(selected.id)
  }, [selected])

  // The notice is shown once: clear it from the history entry after a few seconds.
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(
      () => navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null }),
      NOTICE_MS,
    )
    return () => window.clearTimeout(timer)
  }, [notice, location.pathname, location.search, navigate])

  /** Shows another project by writing its id to `?project=` (replacing the history entry). */
  const pickProject = (projectId: string) => setSearchParams({ [PROJECT_PARAM]: projectId }, { replace: true })

  return (
    <section className="view on" id="v-dashboard">
      <div className="pg">
        <div aria-live="polite">
          {notice && (
            <div className="notice" id="d-notice">
              {notice}
            </div>
          )}
        </div>
        <div className="hdr">
          <div>
            <h1>Dashboard</h1>
            <div className="sub" id="d-welcome">
              {welcomeLine(me.fullName, org.name, projectsQuery.isSuccess ? projects.length : null, Boolean(firstPage?.nextCursor))}
            </div>
          </div>
          <Link className="btn" to="/projects/new">
            + Create Project
          </Link>
        </div>

        {projectsQuery.isPending ? (
          <div className="card small muted" role="status">
            Loading your projects…
          </div>
        ) : projectsQuery.isError ? (
          <div className="cf-banner bad" role="alert">
            <b>&#9888; We couldn&rsquo;t load your projects.</b> {errorMessage(projectsQuery.error)}{' '}
            <button className="linkbtn" type="button" onClick={() => void projectsQuery.refetch()}>
              Try again
            </button>
          </div>
        ) : !selected ? (
          <div className="card" style={{ textAlign: 'center', padding: '40px 24px' }}>
            <h3 style={{ justifyContent: 'center' }}>No projects yet</h3>
            <p className="small muted" style={{ margin: '0 0 16px' }}>
              Create your first project from meeting notes or by hand; its KPIs, risks and next step will show here.
            </p>
            <Link className="btn" to="/projects/new">
              + Create Project
            </Link>
          </div>
        ) : (
          <>
            <div className="card d-picker">
              <div className="field">
                <label htmlFor="d-project">Project</label>
                <select className="in" id="d-project" value={selected.id} onChange={(event) => pickProject(event.target.value)}>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name} &middot; {project.code}
                    </option>
                  ))}
                </select>
              </div>
              <div className="d-meta" id="d-meta">
                <span className="mono small muted">{selected.code}</span>
                <span>
                  <span className="dot" style={{ background: STATUS_COLORS[selected.status] }} aria-hidden="true" />
                  {STATUS_LABELS[selected.status]}
                </span>
                <span>Due {selected.targetEndDate ? formatDate(selected.targetEndDate) : 'not set'}</span>
                <span>PM {selected.pmName ?? 'not set'}</span>
                {overview.data?.appliedVersion != null && (
                  <span className="chip st-stated">Plan v{overview.data.appliedVersion} in force</span>
                )}
              </div>
              <NextStepBox overview={overview.data} failed={overview.isError} projectId={selected.id} />
            </div>

            {overview.isPending ? (
              <div className="card small muted" role="status">
                Loading {selected.name}…
              </div>
            ) : overview.isError ? (
              <div className="cf-banner bad" role="alert">
                <b>&#9888; We couldn&rsquo;t load this project&rsquo;s overview.</b> {errorMessage(overview.error)}{' '}
                <button className="linkbtn" type="button" onClick={() => void overview.refetch()}>
                  Try again
                </button>
              </div>
            ) : (
              <>
                <DashboardKpis overview={overview.data} />
                <div className="dgrid">
                  <div className="stack">
                    <DeliveryHealth overview={overview.data} />
                    <OpenRisks overview={overview.data} />
                  </div>
                  <div className="stack">
                    <ProjectNotifications overview={overview.data} />
                    <AiFindings overview={overview.data} projectId={selected.id} />
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </section>
  )
}

/** "Welcome back, Jane. 3 projects in Acme; pick one…"; the count is left out until it is known. */
function welcomeLine(fullName: string | null, orgName: string, projectCount: number | null, hasMore: boolean): string {
  const firstName = fullName?.trim().split(/\s+/)[0]
  const greeting = `Welcome back${firstName ? `, ${firstName}` : ''}.`
  if (projectCount == null) return greeting
  if (projectCount === 0) return `${greeting} No projects in ${orgName} yet.`
  if (projectCount === 1 && !hasMore) return `${greeting} 1 project in ${orgName}; here is how it is doing.`
  return `${greeting} ${projectCount}${hasMore ? '+' : ''} projects in ${orgName}; pick one to see how it is doing.`
}

/** `#d-next`: the first unfinished workflow step of the shown project, with a link to it. */
function NextStepBox({ overview, failed, projectId }: { overview: ProjectOverview | undefined; failed: boolean; projectId: string }) {
  return (
    <div className="d-nextbox" id="d-next">
      <span className="small muted">Next step</span>
      {overview ? (
        <>
          <b>{overview.nextStep.label}</b>
          <Link className="btn sm" to={stepPath(overview.nextStep.step, projectId)}>
            Open &rarr;
          </Link>
        </>
      ) : (
        <b>{failed ? 'Not available right now' : 'Loading…'}</b>
      )}
    </div>
  )
}
