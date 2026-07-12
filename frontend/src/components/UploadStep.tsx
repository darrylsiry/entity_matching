// Wizard step 2 — upload one file (dedupe) or two files (link). Each dropzone
// posts the file to /api/upload and shows the detected row/column counts; the
// "test data" links one-click-load the bundled sample CSVs.
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: DropZone — one upload target (file picker + optional test-data
// link). Handles its own busy/error state and reports the parsed upload up.
// ════════════════════════════════════════════════════════════════════════

function DropZone({
  label,
  upload,
  onUpload,
  testFile,
}: {
  label: string
  upload: UploadResponse | null
  onUpload: (u: UploadResponse) => void
  // Optional bundled sample file (served from the app root) offered as a quick link.
  testFile?: string
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

  // Fetch the bundled sample CSV and load it into this dropzone as if uploaded.
  async function loadTest(name: string) {
    setError(null)
    try {
      const res = await fetch(`/${name}`)
      if (!res.ok) throw new Error(`could not load ${name}`)
      await handle(new File([await res.blob()], name, { type: 'text/csv' }))
    } catch (e) {
      setError((e as Error).message)
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
      {testFile && (
        <a
          className="test-link"
          href={`/${testFile}`}
          download={testFile}
          title={`Load ${testFile}`}
          onClick={(e) => {
            e.preventDefault()
            loadTest(testFile)
          }}
        >
          test data
        </a>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Step component — renders one or two dropzones by mode and gates
// "Next" until the required file(s) are uploaded.
// ════════════════════════════════════════════════════════════════════════

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
        <DropZone
          label={dedupe ? 'File' : 'File A'}
          upload={uploadA}
          onUpload={onA}
          testFile={dedupe ? undefined : 'companies_file_a.csv'}
        />
        {!dedupe && (
          <DropZone
            label="File B"
            upload={uploadB}
            onUpload={onB}
            testFile="companies_file_b.csv"
          />
        )}
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
