/**
 * Step 2, AI Project Intake (mockup intake.js): paste notes and/or attach documents, run the AI
 * intake, and see what it found before Scope Review.
 *
 * Data: the latest run (`useLatestIntakeRun`, polled while queued/running), the project's documents
 * (`POST/GET/DELETE …/documents`, text extracted server-side), the run history ("Previous Intakes",
 * `GET …/intake/runs`) and the extracted scope items (counted by evidence). Projects created from an
 * upload already had their intake run at creation; the `#in-from-upload` note says so.
 */
import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'
import DocumentUpload, { checkFile, type UploadEntry } from '@/components/DocumentUpload'
import PreviousIntakes from '@/components/PreviousIntakes'
import { errorMessage as describeError, isApiError } from '@/lib/api'
import { uploadSource, type ScopeItemWithEvidence } from '@/lib/create'
import { useDeleteDocument, useDocuments, useLatestIntakeRun, useScopeItems, useStartIntakeRun, useUploadDocument } from '@/lib/queries'
import { useStepNav } from '@/lib/steps'

const EXTRACTS = [
  { tag: 'OBJ', label: 'Objectives', color: '#DDE7D0' },
  { tag: 'REQ', label: 'Requirements', color: '#E3DCCB' },
  { tag: 'DEL', label: 'Deliverables', color: '#F5E3C8' },
  { tag: 'CON', label: 'Constraints', color: '#F3D9D5' },
  { tag: 'RSK', label: 'Risks', color: '#F5E3C8' },
  { tag: 'UNK', label: 'Missing Info', color: '#F3EADB' },
] as const

interface IntakeLocationState {
  uploadError?: string | null
  fromUpload?: boolean
  sourceName?: string
}

/** Notes + documents in, scope items out; Continue to Scope Review once a run has succeeded. */
export default function AIProjectIntake() {
  const goToStep = useStepNav()
  const { projectId = '' } = useParams()
  const location = useLocation()
  const routeState = (location.state as IntakeLocationState | null) ?? {}
  const [text, setText] = useState('')

  const latest = useLatestIntakeRun(projectId)
  const startRun = useStartIntakeRun(projectId)
  const documents = useDocuments(projectId)
  const documentUpload = useUploadDocument(projectId)
  const documentDelete = useDeleteDocument(projectId)
  const scope = useScopeItems(projectId)
  const [uploading, setUploading] = useState<UploadEntry[]>([])
  const [uploadError, setUploadError] = useState<string | null>(routeState.uploadError ?? null)

  useEffect(() => {
    document.title = 'AI Project Intake · NirnAIn'
  }, [])

  const sourceDocuments = documents.data ?? []
  const readyDocuments = sourceDocuments.filter((sourceDocument) => sourceDocument.extractionStatus === 'succeeded').length
  const entries: UploadEntry[] = [
    ...uploading,
    ...sourceDocuments.map((sourceDocument) => ({
      key: sourceDocument.id,
      name: sourceDocument.title,
      sizeBytes: sourceDocument.sizeBytes,
      status: sourceDocument.extractionStatus === 'succeeded' ? ('ready' as const) : ('failed' as const),
      detail: sourceDocument.extractionError,
    })),
  ]

  async function addFiles(files: File[]) {
    setUploadError(null)
    for (const file of files) {
      const problem = checkFile(file)
      if (problem) {
        setUploadError(`${file.name}: ${problem}`)
        continue
      }
      const key = `upload-${file.name}-${file.size}-${Date.now()}`
      setUploading((previous) => [...previous, { key, name: file.name, sizeBytes: file.size, status: 'uploading' }])
      try {
        await documentUpload.mutateAsync(file)
      } catch (error) {
        setUploadError(`${file.name}: ${describeError(error)}`)
      } finally {
        setUploading((previous) => previous.filter((entry) => entry.key !== key))
      }
    }
  }

  function removeFile(key: string) {
    setUploadError(null)
    documentDelete.mutate(key, { onError: (error) => setUploadError(describeError(error)) })
  }

  const run = latest.data ?? null
  const status = run?.status ?? null
  const analyzing = startRun.isPending || status === 'queued' || status === 'running'
  const analyzed = status === 'succeeded'
  const startError = startRun.error && isApiError(startRun.error) ? startRun.error.message : null
  const errorText = startError ?? (status === 'failed' ? (run?.error ?? 'The analysis failed. Try again.') : null)
  const canAnalyze = Boolean(text.trim()) || readyDocuments > 0
  // Created from an upload: the intake ran at creation (this browser remembers the source's name).
  const sourceName = routeState.sourceName ?? (projectId ? uploadSource(projectId) : null)
  const fromUpload = Boolean(run && (routeState.fromUpload || sourceName))

  function handleAnalyze() {
    if (!analyzing && uploading.length === 0 && canAnalyze) startRun.mutate(text)
  }

  return (
    <section className="view on" id="v-intake">
      <div className="pg">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; Create Project &rsaquo; AI Project Intake
        </div>
        <h1>AI Project Intake</h1>
        <div className="sub" style={{ marginBottom: 24 }}>
          Provide raw project notes, meeting transcripts, or documents. AI will extract structured project information.
        </div>
        <div className="in-grid">
          <div>
            {fromUpload && (
              <div className="banner" id="in-from-upload">
                Intake already ran when you created the project from <b id="in-src">{sourceName ?? 'your upload'}</b>. Re-analyze if you add more
                notes or files.
              </div>
            )}
            <div className="card">
              <label className="sl" htmlFor="in-text">
                Brainstorm / Meeting Notes
              </label>
              <textarea
                className="in"
                id="in-text"
                rows={15}
                value={text}
                maxLength={50_000}
                onChange={(event) => setText(event.target.value)}
                placeholder="Paste meeting notes, project description, brainstorming content, or any freeform project context here…"
              />
              <div className="hint">AI will identify objectives, requirements, deliverables, constraints, and risks automatically.</div>
            </div>
            <div className="card mt" data-files="in">
              <div className="sl">Upload Documents</div>
              <DocumentUpload
                entries={entries}
                onFiles={(files) => void addFiles(files)}
                onRemove={removeFile}
                disabled={analyzing}
                error={uploadError ?? (documents.isError ? `Couldn't load this project's documents: ${describeError(documents.error)}` : null)}
                idPrefix="in"
              />
            </div>
            {analyzing && (
              <div className="runbox" id="in-run" role="status">
                <div className={status === 'running' ? 'done' : 'cur'}>Queued</div>
                <div className={status === 'running' ? 'cur' : ''}>
                  Reading the notes{readyDocuments > 0 ? ` and ${readyDocuments} file${readyDocuments > 1 ? 's' : ''}` : ''}, extracting scope (6 categories)
                </div>
                <div>Checking every item against the source</div>
              </div>
            )}
            <div className="row between mt" style={{ marginTop: 20 }}>
              <Link className="btn sec" to="/dashboard">
                &larr; Back to Dashboard
              </Link>
              <div className="row">
                <span
                  id="in-status"
                  role="status"
                  style={{ fontSize: 13, fontWeight: 500, color: errorText ? 'var(--red)' : analyzing ? 'var(--mfg)' : 'var(--green)', maxWidth: 360 }}
                >
                  {analyzing
                    ? 'Analyzing… this can take a minute.'
                    : errorText ?? (analyzed ? `✓ Analysis complete — ${run?.itemsExtracted ?? 0} items extracted` : '')}
                </span>
                <button
                  className="btn lg"
                  id="btn-analyze"
                  type="button"
                  onClick={handleAnalyze}
                  disabled={analyzing || uploading.length > 0 || !canAnalyze}
                  aria-busy={analyzing || undefined}
                >
                  {analyzing ? 'Analyzing…' : run ? 'Re-Analyze' : 'Analyze Project'}
                </button>
                {analyzed && (
                  <button className="btn green" id="btn-to-scope" type="button" onClick={() => goToStep('scope')}>
                    Continue to Scope Review &rarr;
                  </button>
                )}
              </div>
            </div>
            {analyzed && run && (
              <FoundSummary items={scope.data as ScopeItemWithEvidence[] | undefined} loading={scope.isPending} failed={scope.isError} byRules={run.model === 'rules'} />
            )}
          </div>

          <div className="stack">
            <div className="card">
              <div className="sl">What AI Extracts</div>
              <div className="stack" style={{ gap: 8, fontSize: 12 }}>
                {EXTRACTS.map((extract) => (
                  <div key={extract.tag} className="row" style={{ gap: 8 }}>
                    <span className="sc-badge" style={{ background: extract.color }}>
                      {extract.tag}
                    </span>
                    {extract.label}
                  </div>
                ))}
              </div>
            </div>
            <div className="card">
              <div className="sl">Tips for Better Results</div>
              <ul style={{ fontSize: 12, color: '#6B5254', lineHeight: 1.7, paddingLeft: 16, margin: 0 }}>
                <li>Include stakeholder names and roles</li>
                <li>Mention budget figures explicitly</li>
                <li>Note hard deadlines vs. targets</li>
                <li>List known dependencies</li>
                <li>Describe success criteria</li>
              </ul>
            </div>
            <PreviousIntakes projectId={projectId} latest={run} />
          </div>
        </div>
      </div>
    </section>
  )
}

/**
 * "What the AI found": the extracted items by evidence. Counts come from the live scope items;
 * servers without per-item evidence get the plain total instead of invented splits.
 */
function FoundSummary({ items, loading, failed, byRules }: { items: ScopeItemWithEvidence[] | undefined; loading: boolean; failed: boolean; byRules: boolean }) {
  const found = (items ?? []).filter((item) => item.origin === 'ai' && item.status !== 'removed')
  const count = (evidence: string) => found.filter((item) => item.evidence === evidence).length
  const hasEvidence = found.some((item) => item.evidence)
  return (
    <div className="card mt" id="in-result">
      <div className="sl">What the AI found</div>
      {loading ? (
        <p className="small muted" style={{ margin: 0 }}>
          Counting the items…
        </p>
      ) : failed ? (
        <p className="small muted" style={{ margin: 0 }}>
          Couldn&rsquo;t load the items. You&rsquo;ll see them all in Scope Review.
        </p>
      ) : hasEvidence ? (
        <div className="row small">
          <span className="chip st-stated">{count('stated')} Stated</span> quoted word for word &nbsp;
          <span className="chip st-inferred">{count('inferred')} Inferred</span> no direct quote, check them &nbsp;
          <span className="chip st-missing">{count('flagged')} Not in notes</span> say more than your notes do
        </div>
      ) : (
        <p className="small" style={{ margin: 0 }}>
          <b>{found.length} items</b> to review in Scope Review.
        </p>
      )}
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        {byRules
          ? 'Read with simple rules: AI isn’t set up on this server, so check each item closely.'
          : 'Every item keeps the sentence it came from, so you can see why it’s there.'}
      </p>
    </div>
  )
}
