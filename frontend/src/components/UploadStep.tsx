import { useState } from 'react'
import { uploadFile, type UploadResponse } from '../api'

interface Props {
  uploadA: UploadResponse | null
  uploadB: UploadResponse | null
  onA: (u: UploadResponse) => void
  onB: (u: UploadResponse) => void
  onNext: () => void
}

function DropZone({
  label,
  upload,
  onUpload,
}: {
  label: string
  upload: UploadResponse | null
  onUpload: (u: UploadResponse) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handle(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      onUpload(await uploadFile(file))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`dropzone ${upload ? 'filled' : ''}`}>
      <div className="dropzone-label">{label}</div>
      {upload ? (
        <div className="dropzone-meta">
          <strong>{upload.filename}</strong>
          <span>
            {upload.rows.toLocaleString()} rows · {upload.columns.length} columns
          </span>
        </div>
      ) : (
        <div className="dropzone-hint">{busy ? 'Uploading…' : 'CSV or Excel'}</div>
      )}
      <label className="btn btn-ghost">
        {upload ? 'Replace' : 'Choose file'}
        <input
          type="file"
          accept=".csv,.xlsx,.xls"
          hidden
          onChange={(e) => handle(e.target.files?.[0])}
        />
      </label>
      {error && <div className="error">{error}</div>}
    </div>
  )
}

export function UploadStep({ uploadA, uploadB, onA, onB, onNext }: Props) {
  return (
    <div>
      <h2>Upload two files</h2>
      <p className="muted">
        Each file should contain company records — one company per row. We&apos;ll match
        entities in file A against file B.
      </p>
      <div className="upload-grid">
        <DropZone label="File A" upload={uploadA} onUpload={onA} />
        <DropZone label="File B" upload={uploadB} onUpload={onB} />
      </div>
      <div className="actions">
        <span />
        <button className="btn btn-primary" disabled={!uploadA || !uploadB} onClick={onNext}>
          Next: map columns →
        </button>
      </div>
    </div>
  )
}
