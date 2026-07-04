import { useState } from 'react'
import { uploadFile, type Mode, type UploadResponse } from '../api'

interface Props {
  mode: Mode
  uploadA: UploadResponse | null
  uploadB: UploadResponse | null
  onA: (u: UploadResponse) => void
  onB: (u: UploadResponse) => void
  onBack: () => void
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

export function UploadStep({ mode, uploadA, uploadB, onA, onB, onBack, onNext }: Props) {
  const dedupe = mode === 'dedupe'
  const ready = dedupe ? Boolean(uploadA) : Boolean(uploadA && uploadB)

  return (
    <div>
      <h2>{dedupe ? 'Upload your file' : 'Upload two files'}</h2>
      <p className="muted">
        {dedupe
          ? 'One company per row. We’ll find rows that refer to the same company.'
          : 'One company per row in each file. We’ll match records in file A against file B.'}
      </p>
      <div className={dedupe ? 'upload-grid single' : 'upload-grid'}>
        <DropZone label={dedupe ? 'File' : 'File A'} upload={uploadA} onUpload={onA} />
        {!dedupe && <DropZone label="File B" upload={uploadB} onUpload={onB} />}
      </div>
      <div className="actions">
        <button className="btn btn-ghost" onClick={onBack}>
          ← Back
        </button>
        <button className="btn btn-primary" disabled={!ready} onClick={onNext}>
          {dedupe ? 'Next: configure →' : 'Next: map columns →'}
        </button>
      </div>
    </div>
  )
}
