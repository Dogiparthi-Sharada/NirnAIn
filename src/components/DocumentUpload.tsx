import { useId, useRef, useState, type DragEvent } from 'react'

/** Upload tiles from the approved design (mockup views/files.js), shared by Create Project and AI Intake. */
const TILES = [
  { key: 'pdf', icon: '📄', label: 'PDF', sub: 'Reports, specs', accept: '.pdf,application/pdf' },
  {
    key: 'docx',
    icon: '📝',
    label: 'Word / DOCX',
    sub: 'Project docs',
    accept: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  },
  { key: 'transcript', icon: '💬', label: 'Transcript', sub: 'Meeting text', accept: '.txt,.vtt,.srt,.md,text/plain,text/vtt' },
  { key: 'audio', icon: '🎙', label: 'Audio', sub: 'MP3, WAV, M4A', accept: '.mp3,.wav,.m4a,.ogg,.webm,.aac,.flac,audio/*' },
] as const

export type TileKey = (typeof TILES)[number]['key']

const ALL_ACCEPT = TILES.map((t) => t.accept).join(',')
const SUPPORTED_EXTENSIONS = /\.(pdf|docx|txt|vtt|srt|md|mp3|wav|m4a|ogg|webm|aac|flac)$/i
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

export type UploadEntryStatus = 'pending' | 'uploading' | 'ready' | 'failed'

export interface UploadEntry {
  key: string
  name: string
  sizeBytes: number | null
  status: UploadEntryStatus
  /** Shown after the size: extraction errors, "uploads when you continue", … */
  detail?: string | null
}

/** Client-side check before uploading; returns an error message or null. */
export function checkFile(file: File): string | null {
  if (!SUPPORTED_EXTENSIONS.test(file.name)) return 'Unsupported file type. Use PDF, Word (.docx), transcript (.txt, .vtt, .srt) or audio.'
  if (file.size > MAX_UPLOAD_BYTES) return 'Files can be at most 50 MB.'
  return null
}

export function formatSize(bytes: number | null): string {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const STATUS_TEXT: Record<UploadEntryStatus, string> = {
  pending: 'ready to upload',
  uploading: 'uploading…',
  ready: 'text extracted',
  failed: 'not usable',
}

/**
 * Upload tiles + drop zone + attached files. Render it inside the caller's card (`.card` with an
 * "Upload Documents" `.sl` heading), like the mockup's `[data-files]` zones.
 */
export default function DocumentUpload({
  entries,
  onFiles,
  onRemove,
  disabled = false,
  hint = 'Drag files here or click a tile to browse. Max 50 MB per file.',
  error,
  idPrefix,
}: {
  entries: UploadEntry[]
  onFiles: (files: File[]) => void
  onRemove: (key: string) => void
  disabled?: boolean
  hint?: string
  error?: string | null
  /** Gives the tiles and the file list stable ids (`<prefix>-tile-<kind>`, `<prefix>-files`). */
  idPrefix?: string
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const hintId = useId()

  function browse(accept: string) {
    const input = inputRef.current
    if (!input || disabled) return
    input.accept = accept
    input.click()
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragOver(false)
    if (disabled) return
    const files = Array.from(event.dataTransfer.files)
    if (files.length > 0) onFiles(files)
  }

  return (
    <div
      onDragOver={(event) => {
        if (disabled) return
        event.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      style={dragOver ? { outline: '2px dashed var(--pr)', outlineOffset: 4, borderRadius: 6 } : undefined}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ALL_ACCEPT}
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? [])
          event.target.value = ''
          if (files.length > 0) onFiles(files)
        }}
      />
      <div className="tiles">
        {TILES.map((t) => (
          <button
            key={t.key}
            className="tile"
            id={idPrefix ? `${idPrefix}-tile-${t.key}` : undefined}
            type="button"
            onClick={() => browse(t.accept)}
            disabled={disabled}
            aria-describedby={hintId}
            aria-label={`Upload ${t.label} (${t.sub})`}
          >
            <span aria-hidden="true">{t.icon}</span>
            <b>{t.label}</b>
            <span>{t.sub}</span>
          </button>
        ))}
      </div>
      <div className="hint" id={hintId}>
        {hint}
      </div>
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {entries.length > 0 && (
        <ul className="files" id={idPrefix ? `${idPrefix}-files` : undefined} aria-label="Attached files" style={{ listStyle: 'none', padding: 0, margin: '10px 0 0' }}>
          {entries.map((entry) => {
            const meta = [formatSize(entry.sizeBytes), STATUS_TEXT[entry.status], entry.detail].filter(Boolean).join(' · ')
            return (
              <li key={entry.key} className="file">
                <span className="mono" title={entry.name} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                  {entry.name}
                </span>
                <span className="muted" style={entry.status === 'failed' ? { color: 'var(--red)' } : undefined}>
                  {meta}
                </span>
                <button
                  className="x"
                  type="button"
                  onClick={() => onRemove(entry.key)}
                  disabled={entry.status === 'uploading'}
                  aria-label={`Remove ${entry.name}`}
                >
                  ×
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
