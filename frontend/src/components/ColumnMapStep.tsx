import { useEffect } from 'react'
import type { FieldSpec, UploadResponse } from '../api'
import type { Mapping } from '../App'

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

// Heuristic column-name synonyms per canonical field, used to pre-fill the mapping.
const HINTS: Record<string, string[]> = {
  name: ['name', 'company', 'organisation', 'organization', 'legal', 'entity'],
  registration_id: ['ein', 'duns', 'vat', 'reg', 'registration', 'tax', 'number', 'id'],
  website: ['website', 'url', 'site', 'domain', 'web', 'email'],
  address: ['address', 'street', 'addr', 'line1'],
  city: ['city', 'town'],
  state: ['state', 'region', 'province'],
  postcode: ['postcode', 'zip', 'postal'],
  country: ['country', 'nation'],
}

function guess(field: string, columns: string[]): string {
  const hints = HINTS[field] ?? [field]
  const lc = columns.map((c) => c.toLowerCase())
  // exact-ish first, then substring
  for (const h of hints) {
    const i = lc.findIndex((c) => c === h)
    if (i >= 0) return columns[i]
  }
  for (const h of hints) {
    const i = lc.findIndex((c) => c.includes(h))
    if (i >= 0) return columns[i]
  }
  return ''
}

function autoMap(fields: FieldSpec[], upload: UploadResponse): Mapping {
  const cols = upload.columns.map((c) => c.name)
  const m: Mapping = {}
  for (const f of fields) {
    const g = guess(f.field, cols)
    if (g) m[f.field] = g
  }
  return m
}

function FileMapper({
  title,
  upload,
  fields,
  mapping,
  setMapping,
}: {
  title: string
  upload: UploadResponse
  fields: FieldSpec[]
  mapping: Mapping
  setMapping: (m: Mapping) => void
}) {
  const columns = upload.columns.map((c) => c.name)
  function set(field: string, col: string) {
    const next = { ...mapping }
    if (col) next[field] = col
    else delete next[field]
    setMapping(next)
  }
  return (
    <div className="mapper">
      <h3>
        {title} <span className="muted">· {upload.filename}</span>
      </h3>
      <table className="map-table">
        <tbody>
          {fields.map((f) => (
            <tr key={f.field}>
              <td className="map-field">
                {f.label}
                {f.required && <span className="req">*</span>}
              </td>
              <td>
                <select value={mapping[f.field] ?? ''} onChange={(e) => set(f.field, e.target.value)}>
                  <option value="">— not mapped —</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
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
        <FileMapper title="File A" upload={uploadA} fields={fields} mapping={mappingA} setMapping={setMappingA} />
        <FileMapper title="File B" upload={uploadB} fields={fields} mapping={mappingB} setMapping={setMappingB} />
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
