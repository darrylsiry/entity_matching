import { useEffect } from 'react'
import type { FieldSpec, UploadResponse } from '../api'
import { autoMap, type Mapping } from '../mapping'
import { FieldMapper } from './FieldMapper'

interface Props {
  fields: FieldSpec[]
  uploadA: UploadResponse
  uploadB: UploadResponse
  mappingA: Mapping
  mappingB: Mapping
  setMappingA: (m: Mapping) => void
  setMappingB: (m: Mapping) => void
  onBack: () => void
  onNext: () => void
}

export function ColumnMapStep(props: Props) {
  const { fields, uploadA, uploadB, mappingA, mappingB, setMappingA, setMappingB } = props

  // Auto-fill mappings once when the fields/uploads are first available.
  useEffect(() => {
    if (fields.length === 0) return
    if (Object.keys(mappingA).length === 0) setMappingA(autoMap(fields, uploadA))
    if (Object.keys(mappingB).length === 0) setMappingB(autoMap(fields, uploadB))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields, uploadA, uploadB])

  const nameMapped = Boolean(mappingA['name'] && mappingB['name'])

  return (
    <div>
      <h2>Map columns to fields</h2>
      <p className="muted">
        Tell us which column holds each piece of company info. We&apos;ve guessed where we
        can. <strong>Company name</strong> is required; everything else improves accuracy.
      </p>
      <div className="map-grid">
        <FieldMapper title="File A" upload={uploadA} fields={fields} mapping={mappingA} setMapping={setMappingA} />
        <FieldMapper title="File B" upload={uploadB} fields={fields} mapping={mappingB} setMapping={setMappingB} />
      </div>
      {!nameMapped && <div className="error">Map the company name in both files to continue.</div>}
      <div className="actions">
        <button className="btn btn-ghost" onClick={props.onBack}>
          ← Back
        </button>
        <button className="btn btn-primary" disabled={!nameMapped} onClick={props.onNext}>
          Next: configure →
        </button>
      </div>
    </div>
  )
}
