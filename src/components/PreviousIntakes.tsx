/**
 * The "Previous Intakes" card on AI Project Intake: the project's intake runs, newest first.
 *
 * Purpose: The PM sees what each analysis found and whether it ran with AI or the no-AI rules
 * reader; the list refreshes when the latest run starts or finishes, so a re-analysis shows up
 * without a reload.
 * How it fits: Rendered by `screens/AIProjectIntake.tsx`; reads
 * `GET /projects/{projectId}/intake/runs` through `useIntakeRuns`; layer: component.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { formatDateTime } from '@/lib/format'
import { useIntakeRuns } from '@/lib/create'
import type { IntakeRun } from '@/lib/types'

const SHOWN = 6
const ROW_STYLE = { padding: '6px 0', borderBottom: '1px solid var(--muted)' } as const

/** One run's result in words: items found (and whether AI read them), failed, cancelled or running. */
function outcome(run: IntakeRun): string {
  switch (run.status) {
    case 'succeeded':
      // "rules" = the server's no-AI fallback reader; say so rather than pass it off as AI.
      return `${run.itemsExtracted ?? 0} items${run.model === 'rules' ? ' · read with rules, no AI' : ''}`
    case 'failed':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    default:
      return 'running…'
  }
}

/** The run history with loading, empty and error states; `latest` keys the refresh. */
export default function PreviousIntakes({ projectId, latest }: { projectId: string; latest: IntakeRun | null }) {
  const runs = useIntakeRuns(projectId, latest ? `${latest.id}:${latest.status}` : undefined)
  const list = runs.data ?? []

  return (
    <div className="card" aria-busy={runs.isFetching || undefined}>
      <div className="sl">Previous Intakes</div>
      {runs.isPending ? (
        <div className="small muted" style={ROW_STYLE}>
          Loading…
        </div>
      ) : runs.isError ? (
        <div className="small muted" role="alert">
          Couldn&rsquo;t load the previous intakes.{' '}
          <button className="linkbtn" type="button" onClick={() => void runs.refetch()}>
            Try again
          </button>
        </div>
      ) : list.length === 0 ? (
        <div className="small muted">No intakes for this project yet.</div>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {list.slice(0, SHOWN).map((run, index) => (
            <li key={run.id} className="small muted" style={index < Math.min(list.length, SHOWN) - 1 ? ROW_STYLE : { padding: '6px 0' }}>
              {formatDateTime(run.createdAt)} · {outcome(run)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
