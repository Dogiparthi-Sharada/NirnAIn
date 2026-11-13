import { useId, useRef, useState, type CSSProperties, type DragEvent } from 'react'

/** Upload tiles from the AI Project Intake design, shared with Create Project. */
const TILES = [
  { icon: '📄', label: 'PDF', sub: 'Reports, specs', accept: '.pdf,application/pdf' },
  {
    icon: '📝',
    label: 'Word / DOCX',
    sub: 'Project docs',
    accept: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  },
  { icon: '💬', label: 'Transcript', sub: 'Meeting text', accept: '.txt,.vtt,.srt,.md,text/plain,text/vtt' },
  { icon: '🎙', label: 'Audio', sub: 'MP3, WAV, M4A', accept: '.mp3,.wav,.m4a,.ogg,.webm,.aac,.flac,audio/*' },
] as const

const ALL_ACCEPT = TILES.map((t) => t.accept).join(',')
const SUPPORTED_EXTENSIONS = /\.(pdf|docx|txt|vtt|srt|md|mp3|wav|m4a|ogg|webm|aac|flac)$/i
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

export type UploadEntryStatus = 'pending' | 'uploading' | 'ready' | 'failed'

export interface UploadEntry {
  key: string
  name: string
  sizeBytes: number | null
  status: UploadEntryStatus
  /** Shown under the file name: extraction errors, "Uploads after the project is created", … */
  detail?: string | null
}

/** Client-side check before uploading; returns an error message or null. */
export function checkFile(file: File): string | null {
  if (!SUPPORTED_EXTENSIONS.test(file.name)) return 'Unsupported file type. Use PDF, Word (.docx), transcript (.txt, .vtt, .srt) or audio.'
  if (file.size > MAX_UPLOAD_BYTES) return 'Files can be at most 50 MB.'
  return null
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const STATUS_LABEL: Record<UploadEntryStatus, { text: string; color: string }> = {
  pending: { text: 'Ready to upload', color: '#7A6063' },
  uploading: { text: 'Uploading…', color: '#7A6063' },
  ready: { text: '✓ Text extracted', color: '#4F7A4A' },
  failed: { text: 'Not usable', color: '#A83A4A' },
}

export default function DocumentUpload({
  entries,
  onFiles,
  onRemove,
  disabled = false,
  hint = 'Drag files here or click a tile to browse. Max 50 MB per file.',
  error,
}: {
  entries: UploadEntry[]
  onFiles: (files: File[]) => void
  onRemove: (key: string) => void
  disabled?: boolean
  hint?: string
  error?: string | null
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
      style={{ borderRadius: 6, outline: dragOver ? '2px dashed #722F37' : 'none', outlineOffset: 4 }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ALL_ACCEPT}
        style={{ display: 'none' }}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? [])
          event.target.value = ''
          if (files.length > 0) onFiles(files)
        }}
      />
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        {TILES.map((u) => (
          <button
            key={u.label}
            type="button"
            onClick={() => browse(u.accept)}
            disabled={disabled}
            aria-describedby={hintId}
            aria-label={`Upload ${u.label} (${u.sub})`}
            style={{ ...tileStyle, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.6 : 1 }}
          >
            <span style={{ fontSize: 22 }} aria-hidden="true">{u.icon}</span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{u.label}</span>
            <span style={{ fontSize: 11, color: '#999' }}>{u.sub}</span>
          </button>
        ))}
      </div>
      <div id={hintId} style={{ marginTop: 10, fontSize: 11, color: '#999' }}>
        {hint}
      </div>
      {error && (
        <div role="alert" style={{ marginTop: 8, fontSize: 12, color: '#A83A4A' }}>
          {error}
        </div>
      )}
      {entries.length > 0 && (
        <ul aria-label="Attached documents" style={{ listStyle: 'none', margin: '12px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {entries.map((entry) => {
            const status = STATUS_LABEL[entry.status]
            return (
              <li key={entry.key} className="flex items-center justify-between gap-8" style={fileRowStyle}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={entry.name}>
                    {entry.name}
                  </div>
                  <div style={{ fontSize: 11, color: status.color }}>
                    {status.text}
                    {entry.sizeBytes != null && <span style={{ color: '#999' }}> · {formatSize(entry.sizeBytes)}</span>}
                    {entry.detail && <span> · {entry.detail}</span>}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onRemove(entry.key)}
                  disabled={entry.status === 'uploading'}
                  aria-label={`Remove ${entry.name}`}
                  style={removeBtnStyle}
                >
                  Remove
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

const tileStyle: CSSProperties = {
  padding: '14px 10px',
  border: '1px dashed #c8c8c8',
  borderRadius: 6,
  background: '#FAF5EB',
  textAlign: 'center',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 4,
}

const fileRowStyle: CSSProperties = { padding: '7px 10px', border: '1px solid #E8DCC6', borderRadius: 4, background: '#FAF5EB' }

const removeBtnStyle: CSSProperties = {
  flexShrink: 0,
  fontSize: 11,
  padding: '3px 8px',
  background: '#F3D9D5',
  color: '#A83A4A',
  border: '1px solid #A83A4A22',
  borderRadius: 3,
  cursor: 'pointer',
  fontWeight: 500,
}
