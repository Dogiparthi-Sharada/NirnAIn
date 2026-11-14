/**
 * "Report a change" card of step 7: new meeting notes, edited inputs or a progress update.
 *
 * Purpose: Notes are pasted or read from a file and the backend finds what changed (Claude when
 * configured, else its rule-based reader, which never invents values); edits and progress are sent
 * as structured values, never parsed.
 * How it fits: Rendered by `screens/ChangesVersions.tsx`; every pane posts through
 * `useCreateChange`; uses `EditPanes.tsx`; layer: screen.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useRef, useState } from 'react'
import { errorMessage } from '@/lib/api'
import { readNotesFile, useCreateChange, type ChangeEdits, type ChangeRequest, type ChangeSource, type PlanVersion } from '@/lib/changes'
import { EditsPane, ProgressPane } from './EditPanes'
import { RunSteps, useSteps } from './ui'

const READ_STEPS = ['Reading what changed', 'Comparing with the plan in force', 'Finding what it affects']
const TABS: [ChangeSource, string][] = [
  ['notes', 'New meeting notes'],
  ['edits', 'Edit inputs'],
  ['progress', 'Progress update'],
]

type Submit = (body: { source: ChangeSource; text?: string; edits?: ChangeEdits }) => void

/** The tabbed input card; calls `onCreated` with the analysed change so the screen can show its impact. */
export function ReportChange({
  projectId,
  orgId,
  current,
  onCreated,
}: {
  projectId: string
  orgId: string | null
  current: PlanVersion
  onCreated: (change: ChangeRequest) => void
}) {
  const [tab, setTab] = useState<ChangeSource>('notes')
  const create = useCreateChange(projectId)
  const step = useSteps(create.isPending, READ_STEPS.length)
  /** Creates the change request and hands it to the screen. */
  const submit: Submit = (body) => create.mutate(body, { onSuccess: onCreated })

  return (
    <div className="card">
      <div className="row between">
        <h3 style={{ margin: 0 }}>Report a change</h3>
        <span className="small muted">Measured against Plan v{current.version}</span>
      </div>
      <div className="ttabs mt" role="tablist" aria-label="How the change comes in">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            id={`chg-tab-${key}`}
            className={`ttab${tab === key ? ' on' : ''}`}
            data-tab={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            aria-controls={`chg-pane-${key}`}
            onClick={() => {
              setTab(key)
              create.reset()
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div id={`chg-pane-${tab}`} role="tabpanel" aria-labelledby={`chg-tab-${tab}`} data-pane={tab}>
        {tab === 'notes' && <NotesPane orgId={orgId} busy={create.isPending} onSubmit={(text) => submit({ source: 'notes', text })} />}
        {tab === 'edits' && (
          <EditsPane projectId={projectId} current={current} busy={create.isPending} onSubmit={(edits) => submit({ source: 'edits', edits })} />
        )}
        {tab === 'progress' && (
          <ProgressPane projectId={projectId} current={current} busy={create.isPending} onSubmit={(edits) => submit({ source: 'progress', edits })} />
        )}
      </div>
      {create.isPending && <RunSteps steps={READ_STEPS} at={step} />}
      {create.isError && (
        <div className="alert bad mt" role="alert">
          {errorMessage(create.error)}
        </div>
      )}
    </div>
  )
}

/** Paste or upload notes; uploaded text is appended so several files can be combined. */
function NotesPane({ orgId, busy, onSubmit }: { orgId: string | null; busy: boolean; onSubmit: (text: string) => void }) {
  const [text, setText] = useState('')
  const [reading, setReading] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Why "Find what changed" can't run yet (null = it can).
  let blocked: string | null = null
  if (busy) blocked = 'Reading the notes…'
  else if (reading) blocked = 'Reading the file…'
  else if (!text.trim()) blocked = 'Paste the meeting notes or upload a file first.'

  /** Reads an uploaded notes file and appends its text to the notes. */
  const onFile = async (file: File | undefined) => {
    if (!file) return
    setFileError(null)
    setReading(true)
    try {
      const content = await readNotesFile(file, orgId)
      setText((previous) => (previous.trim() ? `${previous}\n\n${content}` : content))
    } catch (error) {
      setFileError(errorMessage(error))
    } finally {
      setReading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <>
      <label className="sr-only" htmlFor="chg-notes">
        New meeting notes
      </label>
      <textarea
        className="in"
        rows={9}
        id="chg-notes"
        value={text}
        maxLength={50000}
        placeholder="Paste the notes from the meeting where something changed: a new budget, a moved deadline, work that slips or is done, someone leaving…"
        onChange={(event) => setText(event.target.value)}
      />
      <div className="row between">
        <div className="hint">Or upload a transcript, PDF, Word or text file, same as Create Project.</div>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept=".txt,.md,.vtt,.srt,.csv,.pdf,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={(event) => void onFile(event.target.files?.[0])}
        />
        <button className="btn sm sec mt8" type="button" disabled={reading || busy} onClick={() => fileRef.current?.click()}>
          {reading ? 'Reading file…' : 'Upload a file'}
        </button>
      </div>
      {fileError && (
        <div className="note bad" role="alert">
          {fileError}
        </div>
      )}
      <div className="row end mt">
        {blocked && (
          <span className="small muted" id="chg-find-why">
            {blocked}
          </span>
        )}
        <button
          className="btn"
          data-find="notes"
          type="button"
          disabled={blocked !== null}
          aria-describedby={blocked ? 'chg-find-why' : undefined}
          title={blocked ?? undefined}
          onClick={() => onSubmit(text)}
        >
          Find what changed
        </button>
      </div>
    </>
  )
}
