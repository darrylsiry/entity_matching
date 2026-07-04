import type { SessionConfig } from '../api'

interface Props {
  config: SessionConfig
  setConfig: (c: SessionConfig) => void
  onBack: () => void
  onNext: () => void
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

export function ConfigStep({ config, setConfig, onBack, onNext }: Props) {
  function toggle(key: (typeof STAGES)[number]['key']) {
    setConfig({ ...config, [key]: !config[key] })
  }
  function setThreshold(which: 'match' | 'review', value: number) {
    setConfig({ ...config, thresholds: { ...config.thresholds, [which]: value } })
  }

  const anyStage = config.use_deterministic || config.use_probabilistic || config.use_fuzzy

  return (
    <div>
      <h2>Configure matching</h2>
      <p className="muted">
        Records flow through these stages in series; each stage only sees what earlier
        ones didn&apos;t confidently resolve.
      </p>

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
      <div className="actions">
        <button className="btn btn-ghost" onClick={onBack}>
          ← Back
        </button>
        <button className="btn btn-primary" disabled={!anyStage} onClick={onNext}>
          Next: run →
        </button>
      </div>
    </div>
  )
}
