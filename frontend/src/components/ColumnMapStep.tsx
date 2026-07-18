// Wizard step 3 (link mode only) — map each file's columns onto the canonical
// fields. Mappings are auto-guessed from the headers on first render; a
// registration-ID *type* can be declared per file so cross-type numeric
// collisions (DUNS vs EIN) never join.
import { useEffect, useId } from 'react'
import type { FieldSpec, RegistrationIdType, UploadResponse } from '../api'
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
  idTypeA: RegistrationIdType
  idTypeB: RegistrationIdType
  setIdTypeA: (t: RegistrationIdType) => void
  setIdTypeB: (t: RegistrationIdType) => void
  onBack: () => void
  onNext: () => void
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Registration-ID type select — shown only when a reg-id column is
// mapped; the choice is sent with the session (backend guard, spec 1.7).
// ════════════════════════════════════════════════════════════════════════

const ID_TYPES: { value: RegistrationIdType; label: string }[] = [
  { value: 'unknown', label: 'Unknown / mixed' },
  { value: 'duns', label: 'DUNS' },
  { value: 'ein', label: 'EIN' },
  { value: 'vat', label: 'VAT' },
  { value: 'company_number', label: 'Company number' },
  { value: 'lei', label: 'LEI' },
  { value: 'other', label: 'Other' },
]

function IdTypeSelect({
  value,
  onChange,
}: {
  value: RegistrationIdType
  onChange: (t: RegistrationIdType) => void
}) {
  const selectId = useId()
  return (
    <div className="id-type-row">
      <label className="muted small" htmlFor={selectId}>
        ID type
      </label>
      <select
        id={selectId}
        value={value}
        onChange={(e) => onChange(e.target.value as RegistrationIdType)}
      >
        {ID_TYPES.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Step component — two FieldMapper tables (one per file), auto-fill
// on mount, a warning when declared id types differ, and a "name is required"
// gate on the Next button.
// ════════════════════════════════════════════════════════════════════════

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
  // Different declared id types on the two files means numeric collisions (a DUNS
  // matching an EIN) won't be treated as the same company.
  const idTypesDiffer =
    props.idTypeA !== 'unknown' && props.idTypeB !== 'unknown' && props.idTypeA !== props.idTypeB

  return (
    <div>
      <h2>Map columns to fields</h2>
      <p className="muted">
        Tell us which column holds each piece of company info. We&apos;ve guessed where we can.{' '}
        <strong>Company name</strong> is required; everything else improves accuracy.
      </p>
      <div className="map-grid">
        <div>
          <FieldMapper
            title="File A"
            upload={uploadA}
            fields={fields}
            mapping={mappingA}
            setMapping={setMappingA}
          />
          {mappingA['registration_id'] && (
            <IdTypeSelect value={props.idTypeA} onChange={props.setIdTypeA} />
          )}
        </div>
        <div>
          <FieldMapper
            title="File B"
            upload={uploadB}
            fields={fields}
            mapping={mappingB}
            setMapping={setMappingB}
          />
          {mappingB['registration_id'] && (
            <IdTypeSelect value={props.idTypeB} onChange={props.setIdTypeB} />
          )}
        </div>
      </div>
      {idTypesDiffer && (
        <p className="muted small review-hint">
          The two files declare different ID types ({props.idTypeA} vs {props.idTypeB}), so IDs
          won&apos;t be joined across them — matching will rely on the other fields.
        </p>
      )}
      {!nameMapped && (
        <div className="error" role="alert">
          Map the company name in both files to continue.
        </div>
      )}
      <div className="actions">
        <button className="btn btn-ghost" onClick={props.onBack}>
          <span aria-hidden="true">←</span> Back
        </button>
        <button className="btn btn-primary" disabled={!nameMapped} onClick={props.onNext}>
          Next: configure <span aria-hidden="true">→</span>
        </button>
      </div>
    </div>
  )
}
