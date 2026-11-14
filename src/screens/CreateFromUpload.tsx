/**
 * Step 1b, Create Project from a meeting transcript or document (mockup create-upload.js).
 *
 * Input: one file (PDF, Word, transcript) and/or pasted notes. "Read it" sends the source to
 * `POST /orgs/{orgId}/project-drafts/extract`, which saves nothing and returns each project value
 * with its quote and status. Review: the PM checks every value in `DraftReviewForm`; an amber
 * banner lists what still needs them (missing values, unconfirmed inferences) and turns red when
 * Create is clicked too early. Create is never disabled. Create then makes the project (step
 * 'scope'), keeps the file as a project document, starts the AI intake on it and opens Scope
 * Review with router state `{ fromUpload: true, sourceName }` (see `useCreateFromDraft`).
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { GapBanner, StatusChip, useBannerFocus } from '@/components/CreateForm'
import DocumentUpload, { checkFile } from '@/components/DocumentUpload'
import DraftReviewForm, { type ReviewSettings } from '@/components/DraftReviewForm'
import { errorMessage } from '@/lib/api'
import { DEPARTMENTS } from '@/lib/createForm'
import {
  MAX_NOTES_CHARS,
  rememberUploadSource,
  useCreateFromDraft,
  useExtractDraft,
  usePmOptions,
  type ProjectDraft,
} from '@/lib/create'
import { initialReview, REVIEW_IDS, REVIEW_LABELS, reviewedFields, reviewNeeds, type ReviewIntent, type ReviewKey, type ReviewState } from '@/lib/draftReview'
import { useOrg } from '@/org/useOrg'

const AUDIO = /\.(mp3|wav|m4a|ogg|webm|aac|flac)$/i
const AUDIO_MESSAGE = 'Audio transcription is not available yet. Upload a transcript of the meeting instead.'

/** Upload or paste → review the pre-filled project → create it and start the intake (nothing is saved before Create / Save Draft). */
export default function CreateFromUpload() {
  const navigate = useNavigate()
  const { me, org } = useOrg()
  const pm = usePmOptions()
  const extract = useExtractDraft(org.id)
  const create = useCreateFromDraft()

  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [draft, setDraft] = useState<ProjectDraft | null>(null)
  const [review, setReview] = useState<ReviewState | null>(null)
  const [settings, setSettings] = useState<ReviewSettings>({
    type: 'technology',
    pmUserId: me.id,
    priority: 'medium',
    methodology: 'waterfall',
    visibility: 'internal',
  })
  /** Set by a blocked click: the banner turns red and lists that intent's blockers. */
  const [tried, setTried] = useState<ReviewIntent | null>(null)
  const [saving, setSaving] = useState<ReviewIntent | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const banner = useBannerFocus()

  useEffect(() => {
    document.title = 'Create Project from a document · NirnAIn'
  }, [])

  function attach(files: File[]) {
    setFileError(null)
    const picked = files[0]
    if (!picked) return
    const problem = AUDIO.test(picked.name) ? AUDIO_MESSAGE : checkFile(picked)
    if (problem) {
      setFileError(`${picked.name}: ${problem}`)
      return
    }
    setFile(picked)
    if (files.length > 1) setFileError(`One file at a time: reading ${picked.name}.`)
  }

  function read() {
    if (!file && !notes.trim()) return
    extract.mutate(file ? { file } : { text: notes }, {
      onSuccess: (result) => {
        setDraft(result)
        setReview(initialReview(result, DEPARTMENTS[0]))
        setTried(null)
        setSaveError(null)
      },
    })
  }

  function backToSource() {
    setDraft(null)
    setReview(null)
    extract.reset()
  }

  function change(key: ReviewKey, value: string) {
    setReview((previous) => (previous ? { ...previous, [key]: { ...previous[key], value } } : previous))
  }

  function confirm(key: ReviewKey) {
    setReview((previous) => (previous ? { ...previous, [key]: { ...previous[key], confirmed: true } } : previous))
  }

  function jump(key: string) {
    const element = document.getElementById(REVIEW_IDS[key as ReviewKey])
    element?.scrollIntoView({ block: 'center' })
    element?.focus()
  }

  async function save(intent: ReviewIntent) {
    if (!draft || !review || saving) return
    if (reviewNeeds(review, intent).length > 0) {
      setTried(intent)
      banner.request()
      return
    }
    setSaving(intent)
    setSaveError(null)
    try {
      const { project, problem } = await create.mutateAsync({
        orgId: org.id,
        project: {
          ...reviewedFields(review),
          ...settings,
          pmUserId: settings.pmUserId || null,
          currency: 'USD',
          status: intent === 'draft' ? 'draft' : 'planning',
          currentStep: intent === 'draft' ? 'intake' : 'scope',
        },
        draft,
        file,
        // Pasted notes next to a file are read by the intake too; on their own they are the draft's text.
        notes: file ? notes : '',
      })
      rememberUploadSource(project.id, draft.sourceName)
      if (intent === 'draft') {
        navigate('/dashboard', { state: { notice: [`Draft “${project.name}” saved.`, problem].filter(Boolean).join(' ') } })
      } else {
        navigate(`/projects/${encodeURIComponent(project.id)}/scope`, {
          state: { fromUpload: true, sourceName: draft.sourceName, problem },
        })
      }
    } catch (error) {
      setSaving(null)
      setSaveError(errorMessage(error))
      banner.request()
    }
  }

  const needs = review ? reviewNeeds(review, tried ?? 'create') : []
  const createNeeds = review ? reviewNeeds(review, 'create') : []
  const invalid = new Set<ReviewKey>(tried ? needs.map((need) => need.key) : [])
  const sourceLabel = file ? file.name : 'Pasted notes'
  const byRules = draft?.model === 'rules'

  return (
    <section className="view on" id="v-create-upload">
      <div className="pg narrow">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; <Link to="/projects/new">Create Project</Link> &rsaquo; From a transcript or document
        </div>
        <h1>Create New Project</h1>
        <div className="sub" style={{ marginBottom: 20 }}>
          Start from a meeting transcript or document. AI reads it and fills in the project for you to check.
        </div>

        <div id="up-input" hidden={draft !== null}>
          <div className="card" data-files="up">
            <div className="sl">Upload Documents</div>
            <DocumentUpload
              entries={file ? [{ key: 'source', name: file.name, sizeBytes: file.size, status: 'pending' }] : []}
              onFiles={attach}
              onRemove={() => setFile(null)}
              disabled={extract.isPending}
              hint="Drag a file here or click a tile to browse: PDF, Word or a transcript (.txt, .vtt, .srt). Max 50 MB."
              error={fileError}
              idPrefix="up"
            />
          </div>
          <div className="card mt">
            <label className="sl" htmlFor="up-text">
              {file ? 'Add notes (optional)' : 'Or paste notes'}
            </label>
            <textarea
              className="in"
              id="up-text"
              rows={4}
              maxLength={MAX_NOTES_CHARS}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Paste meeting notes, project description, brainstorming content, or any freeform project context here…"
            />
            {file && notes.trim() && <div className="hint">Project details are read from {file.name}; AI intake reads these notes too.</div>}
          </div>
          <div className="row between mt">
            <Link className="btn sec" to="/projects/new">
              &larr; Choose a different start
            </Link>
            <button className="btn" id="btn-read" type="button" onClick={read} disabled={(!file && !notes.trim()) || extract.isPending} aria-busy={extract.isPending || undefined}>
              {extract.isPending ? 'Reading…' : 'Read it and fill in the project'}
            </button>
          </div>
          {extract.isPending && (
            <div className="runbox" id="up-run" role="status">
              <div className="cur">Reading {sourceLabel} and finding the project details</div>
              <div>Checking every value against the source</div>
            </div>
          )}
          {extract.isError && (
            <div className="cf-banner bad mt" role="alert">
              <b>&#9888; We couldn&rsquo;t read {sourceLabel}.</b> {errorMessage(extract.error)}
            </div>
          )}
        </div>

        {draft && review && (
          <div id="up-review">
            <div className="banner">
              <b>{byRules ? 'Here’s what we found.' : 'Here’s what AI found.'}</b> Check each field. Nothing is saved until you click <b>Create project</b>.{' '}
              <span id="up-src" className="muted">
                Source: {draft.sourceName}
                {byRules && ' · Read with simple rules: AI isn’t set up on this server, so more values may be left for you.'}
              </span>
            </div>
            <GapBanner
              id="pf-banner"
              tone={tried || saveError ? 'bad' : 'warn'}
              ref={banner.ref}
              error={saveError ? { title: 'We couldn’t create the project', detail: saveError } : null}
              lead={`${tried === 'draft' ? 'Can’t save the draft yet.' : tried ? 'Can’t create the project yet.' : 'Needs you before the project can be created.'} ${needs.length} value${needs.length > 1 ? 's' : ''} to check:`}
              items={needs.map((need) => ({ key: need.key, label: REVIEW_LABELS[need.key], message: need.message }))}
              onJump={jump}
            />

            <DraftReviewForm
              review={review}
              sourceName={draft.sourceName}
              invalid={invalid}
              onChange={change}
              onConfirm={confirm}
              settings={settings}
              onSettings={(patch) => setSettings((previous) => ({ ...previous, ...patch }))}
              pmOptions={pm.options}
            />

            <div className="card mt">
              <div className="sl">Also found</div>
              <p className="small" style={{ margin: 0 }}>
                When you create the project, AI also pulls the scope out of the same source: objectives, requirements, deliverables,
                constraints, risks and open questions, each with the sentence it came from. You&rsquo;ll review them next.
              </p>
            </div>

            <div className="cf-actions">
              <button className="btn sec" id="btn-up-back" type="button" onClick={backToSource}>
                &larr; Change the source
              </button>
              <div className="row">
                <StatusChip id="pf-hint" todo={createNeeds.length ? `${createNeeds.length} value${createNeeds.length > 1 ? 's' : ''} to check` : null} ready="Ready to create" />
                <button className="btn sec" id="btn-up-draft" type="button" onClick={() => void save('draft')} aria-busy={saving === 'draft' || undefined}>
                  {saving === 'draft' ? 'Saving…' : 'Save Draft'}
                </button>
                <button className="btn" id="btn-up-create" type="button" onClick={() => void save('create')} aria-busy={saving === 'create' || undefined}>
                  {saving === 'create' ? 'Creating…' : 'Create project & review scope →'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
