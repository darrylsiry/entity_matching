import { useState } from 'react'
import type { FieldSpec, Mode, SessionConfig, UploadResponse } from '../api'
import type { Mapping } from '../mapping'
import { FieldMapper } from './FieldMapper'

interface Props {
  mode: Mode
  config: SessionConfig
  setConfig: (c: SessionConfig) => void
  onBack: () => void
  onNext: () => void
  // Dedupe-only: lets the user confirm/adjust the auto-detected field mapping.
  fields?: FieldSpec[]
  upload?: UploadResponse | null
  mapping?: Mapping
  setMapping?: (m: Mapping) => void
}

const STAGES = [
  {
    key: 'use_deterministic' as const,
    title: '1 · Deterministic',
    desc: 'Exact match on registration/tax ID, then website domain. Near-certain.',
  },
  {
    key: 'use_probabilistic' as const,
    title: '2 · Probabilistic (Splink)',
    desc: 'Blocked Fellegi-Sunter linkage over all fields. The workhorse at scale.',
  },
  {
    key: 'use_fuzzy' as const,
    title: '3 · Fuzzy fallback',
    desc: 'rapidfuzz name similarity for anything the earlier stages missed.',
  },
]

export function ConfigStep({
  mode, config, setConfig, onBack, onNext, fields, upload, mapping, setMapping,
}: Props) {
  const [showFields, setShowFields] = useState(false)

  function toggle(key: (typeof STAGES)[number]['key']) {
    setConfig({ ...config, [key]: !config[key] })
  }
  function setThreshold(which: 'match' | 'review', value: number) {
    setConfig({ ...config, thresholds: { ...config.thresholds, [which]: value } })
  }

  const anyStage = config.use_deterministic || config.use_probabilistic || config.use_fuzzy

  const canEditFields = Boolean(mode === 'dedupe' && fields && upload && mapping && setMapping)
  const nameOk = mode !== 'dedupe' || Boolean(mapping?.['name'])
  const canContinue = anyStage && nameOk

  return (
    <div>
      <h2>Configure matching</h2>
      <p className="muted">
        Records flow through these stages in series; each stage only sees what earlier
        ones didn&apos;t confidently resolve.
      </p>

      {canEditFields && (
        <DetectedFields
          fields={fields!}
          upload={upload!}
          mapping={mapping!}
          setMapping={setMapping!}
          open={showFields}
          onToggle={() => setShowFields((v) => !v)}
        />
      )}

      <div className="stage-list">
        {STAGES.map((s) => (
          <label key={s.key} className={`stage ${config[s.key] ? 'on' : ''}`}>
            <input type="checkbox" checked={config[s.key]} onChange={() => toggle(s.key)} />
            <div>
              <div className="stage-title">{s.title}</div>
              <div className="muted">{s.desc}</div>
            </div>
          </label>
        ))}
      </div>

      <div className="thresholds">
        <h3>Classification thresholds</h3>
        <div className="slider-row">
          <label>Match ≥ {config.thresholds.match.toFixed(2)}</label>
          <input
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={config.thresholds.match}
            onChange={(e) => setThreshold('match', Number(e.target.value))}
          />
        </div>
        <div className="slider-row">
          <label>Review ≥ {config.thresholds.review.toFixed(2)}</label>
          <input
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={config.thresholds.review}
            onChange={(e) => setThreshold('review', Number(e.target.value))}
          />
        </div>
        <p className="muted small">
          Pairs scoring at or above <em>Match</em> are auto-accepted; between{' '}
          <em>Review</em> and <em>Match</em> are flagged for a human; below are dropped.
        </p>
      </div>

      {!anyStage && <div className="error">Enable at least one stage.</div>}
      {!nameOk && (
        <div className="error">
          Company name isn&apos;t detected — open “Detected fields” above and pick the
          name column to continue.
        </div>
      )}
      <div className="actions">
        <button className="btn btn-ghost" onClick={onBack}>
          ← Back
        </button>
        <button className="btn btn-primary" disabled={!canContinue} onClick={onNext}>
          Next: run →
        </button>
      </div>
    </div>
  )
}

function DetectedFields({
  fields, upload, mapping, setMapping, open, onToggle,
}: {
  fields: FieldSpec[]
  upload: UploadResponse
  mapping: Mapping
  setMapping: (m: Mapping) => void
  open: boolean
  onToggle: () => void
}) {
  // One-line summary of what was auto-detected, e.g. "name → Company, domain → URL".
  const detected = fields
    .filter((f) => mapping[f.field])
    .map((f) => `${f.label} → ${mapping[f.field]}`)

  return (
    <div className="detected">
      <div className="detected-head">
        <div>
          <strong>Detected fields</strong>{' '}
          <span className="muted small">
            {detected.length ? detected.join(' · ') : 'none detected'}
          </span>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onToggle}>
          {open ? 'Done' : 'Adjust'}
        </button>
      </div>
      {open && (
        <div className="detected-body">
          <p className="muted small">
            We guessed these from your column names. Fix any that are wrong —{' '}
            <strong>Company name</strong> is required; the rest improve accuracy.
          </p>
          <FieldMapper upload={upload} fields={fields} mapping={mapping} setMapping={setMapping} />
        </div>
      )}
    </div>
  )
}
