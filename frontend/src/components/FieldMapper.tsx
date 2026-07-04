import type { FieldSpec, UploadResponse } from '../api'
import type { Mapping } from '../mapping'

// Reusable canonical-field -> column dropdown table. Used both by the two-file
// column-mapping step and by the single-file dedupe "confirm detected fields" panel.
export function FieldMapper({
  title,
  upload,
  fields,
  mapping,
  setMapping,
}: {
  title?: string
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
      {title && (
        <h3>
          {title} <span className="muted">· {upload.filename}</span>
        </h3>
      )}
      <table className="map-table">
        <tbody>
          {fields.map((f) => (
            <tr key={f.field}>
              <td className="map-field">
                {f.label}
                {f.required && <span className="req">*</span>}
              </td>
              <td>
                <select
                  value={mapping[f.field] ?? ''}
                  onChange={(e) => set(f.field, e.target.value)}
                >
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
