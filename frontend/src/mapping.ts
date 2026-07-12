// Heuristic column-name -> canonical-field guessing, shared by the column-mapping
// step (two-file mode) and the auto-map used in single-file dedupe mode.
import type { FieldSpec, UploadResponse } from './api'

export type Mapping = Record<string, string>

// ════════════════════════════════════════════════════════════════════════
// SECTION: Header-name hints — per canonical field, the substrings we look
// for in a column header (exact match tried first, then substring).
// ════════════════════════════════════════════════════════════════════════

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

export function guess(field: string, columns: string[]): string {
  const hints = HINTS[field] ?? [field]
  const lc = columns.map((c) => c.toLowerCase())
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

export function autoMap(fields: FieldSpec[], upload: UploadResponse): Mapping {
  const cols = upload.columns.map((c) => c.name)
  const m: Mapping = {}
  for (const f of fields) {
    const g = guess(f.field, cols)
    if (g) m[f.field] = g
  }
  return m
}
