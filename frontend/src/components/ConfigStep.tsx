// Wizard "Configure" step — toggle the cascade stages, set the Match/Review
// thresholds, tune advanced options, and (dedupe mode) confirm the auto-
// detected field mapping. All choices land in the shared SessionConfig.
import { useId, useState } from 'react'
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Stage toggles — the three cascade stages the user can turn on/off
// ════════════════════════════════════════════════════════════════════════

const STAGES = [
  {
    key: 'use_deterministic' as const,
    stage: 'deterministic',
    title: '1 · Deterministic',
    desc: 'Exact match on registration/tax ID, then website domain. Near-certain.',
  },
  {
    key: 'use_probabilistic' as const,
    stage: 'probabilistic',
    title: '2 · Probabilistic (Splink)',
    desc: 'Blocked Fellegi-Sunter linkage over all fields. The workhorse at scale.',
  },
  {
    key: 'use_fuzzy' as const,
    stage: 'fuzzy',
    title: '3 · Fuzzy fallback',
    desc: 'rapidfuzz name similarity for anything the earlier stages missed.',
  },
]

type StageOverride = { match?: number | null; review?: number | null }

// ════════════════════════════════════════════════════════════════════════
// SECTION: Step component — stage checkboxes, threshold sliders, the advanced
// panel, and (dedupe) the detected-fields panel; gates Next on "at least one
// stage enabled" and (dedupe) "name field detected".
// ════════════════════════════════════════════════════════════════════════

export function ConfigStep({
  mode,
  config,
  setConfig,
  onBack,
  onNext,
  fields,
  upload,
  mapping,
  setMapping,
}: Props) {
  const [showFields, setShowFields] = useState(false)

  function toggle(key: (typeof STAGES)[number]['key']) {
    setConfig({ ...config, [key]: !config[key] })
  }
  function setThreshold(which: 'match' | 'review', value: number) {
    setConfig({ ...config, thresholds: { ...config.thresholds, [which]: value } })
  }

  const anyStage = config.use_deterministic || config.use_probabilistic || config.use_fuzzy
  const reviewAboveMatch = config.thresholds.review > config.thresholds.match
  const matchSliderId = useId()
  const reviewSliderId = useId()

  // Per-stage threshold overrides (advanced; defaults-first). A stage with no
  // entry inherits the global sliders.
  const overrides = config.stage_thresholds ?? {}
  const [openStage, setOpenStage] = useState<string | null>(null)
  function setStageOverride(stage: string, o: StageOverride | null) {
    const next: Record<string, StageOverride> = { ...(config.stage_thresholds ?? {}) }
    if (o === null) delete next[stage]
    else next[stage] = o
    setConfig({ ...config, stage_thresholds: Object.keys(next).length > 0 ? next : null })
  }
  const badStageOverride = STAGES.find((s) => {
    const o = overrides[s.stage]
    if (!o) return false
    const m = o.match ?? config.thresholds.match
    const r = o.review ?? config.thresholds.review
    return r > m
  })

  const canEditFields = Boolean(mode === 'dedupe' && fields && upload && mapping && setMapping)
  const nameOk = mode !== 'dedupe' || Boolean(mapping?.['name'])
  const canContinue = anyStage && nameOk && !reviewAboveMatch && !badStageOverride

  return (
    <div>
      <h2>Configure matching</h2>
      <p className="muted">
        Records flow through these stages in series; each stage only sees what earlier ones
        didn&apos;t confidently resolve.
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
        {STAGES.map((s) => {
          const override = overrides[s.stage]
          const open = openStage === s.stage
          return (
            <div key={s.key} className={`stage-card ${config[s.key] ? 'on' : ''}`}>
              <div className="stage-head">
                <label className="stage-main">
                  <input
                    type="checkbox"
                    checked={config[s.key]}
                    onChange={() => toggle(s.key)}
                    aria-label={s.title}
                  />
                  <div>
                    <div className="stage-title">{s.title}</div>
                    <div className="muted">{s.desc}</div>
                  </div>
                </label>
                {config[s.key] && (
                  <button
                    className={`btn btn-ghost btn-sm stage-gear ${override ? 'overridden' : ''}`}
                    aria-expanded={open}
                    aria-label={`Adjust thresholds for ${s.title}`}
                    title="Per-stage thresholds"
                    onClick={() => setOpenStage(open ? null : s.stage)}
                  >
                    {override ? (
                      <>
                        ≥{(override.match ?? config.thresholds.match).toFixed(2)} / ≥
                        {(override.review ?? config.thresholds.review).toFixed(2)}
                      </>
                    ) : (
                      <span aria-hidden="true">⚙</span>
                    )}
                  </button>
                )}
              </div>
              {config[s.key] && open && (
                <StageThresholdEditor
                  stageTitle={s.title}
                  override={override}
                  globalMatch={config.thresholds.match}
                  globalReview={config.thresholds.review}
                  onChange={(o) => setStageOverride(s.stage, o)}
                />
              )}
            </div>
          )
        })}
      </div>

      <div className="thresholds">
        <h3>Classification thresholds — applied to every stage&apos;s final score</h3>
        <p className="muted small">
          Each stage scores its candidate pairs from 0 to 1; at the end of the run, every
          pair&apos;s score — whichever stage produced it — is classified against these two cutoffs.
          Exact ID and website/phone hits from the deterministic stage score 0.99–1.0, so they
          auto-match at any normal setting; the sliders mostly carve up the probabilistic and fuzzy
          scores.
        </p>
        <div className="slider-row">
          <label htmlFor={matchSliderId}>Match ≥ {config.thresholds.match.toFixed(2)}</label>
          <input
            id={matchSliderId}
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={config.thresholds.match}
            onChange={(e) => setThreshold('match', Number(e.target.value))}
          />
        </div>
        <div className="slider-row">
          <label htmlFor={reviewSliderId}>Review ≥ {config.thresholds.review.toFixed(2)}</label>
          <input
            id={reviewSliderId}
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={config.thresholds.review}
            onChange={(e) => setThreshold('review', Number(e.target.value))}
          />
        </div>
        <p className="muted small">
          Pairs scoring at or above <em>Match</em> are auto-accepted; between <em>Review</em> and{' '}
          <em>Match</em> are flagged for a human; below are dropped.
        </p>
        {reviewAboveMatch && (
          <div className="error" role="alert">
            Review ({config.thresholds.review.toFixed(2)}) is above Match (
            {config.thresholds.match.toFixed(2)}) — nothing would land in the review band. Lower
            Review or raise Match.
          </div>
        )}
        {badStageOverride && (
          <div className="error" role="alert">
            {badStageOverride.title}: its Review override is above its Match cutoff — nothing from
            that stage would land in the review band. Fix it in the stage&apos;s ⚙ panel.
          </div>
        )}
      </div>

      <AdvancedPanel config={config} setConfig={setConfig} />

      {!anyStage && (
        <div className="error" role="alert">
          Enable at least one stage.
        </div>
      )}
      {!nameOk && (
        <div className="error" role="alert">
          Company name isn&apos;t detected — open “Detected fields” above and pick the name column
          to continue.
        </div>
      )}
      <div className="actions">
        <button className="btn btn-ghost" onClick={onBack}>
          <span aria-hidden="true">←</span> Back
        </button>
        <button className="btn btn-primary" disabled={!canContinue} onClick={onNext}>
          Next: run <span aria-hidden="true">→</span>
        </button>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Per-stage threshold editor — inline strip under a stage card.
// Defaults-first: a stage with no override inherits the global sliders; turning
// the toggle off seeds the stage's own sliders from the current globals.
// ════════════════════════════════════════════════════════════════════════

function StageThresholdEditor({
  stageTitle,
  override,
  globalMatch,
  globalReview,
  onChange,
}: {
  stageTitle: string
  override: StageOverride | undefined
  globalMatch: number
  globalReview: number
  onChange: (o: StageOverride | null) => void
}) {
  const useGlobalId = useId()
  const matchId = useId()
  const reviewId = useId()
  const m = override?.match ?? globalMatch
  const r = override?.review ?? globalReview
  return (
    <div className="stage-tweak">
      <label className="stage-tweak-toggle" htmlFor={useGlobalId}>
        <input
          id={useGlobalId}
          type="checkbox"
          checked={!override}
          onChange={(e) =>
            onChange(e.target.checked ? null : { match: globalMatch, review: globalReview })
          }
        />
        Use global thresholds ({globalMatch.toFixed(2)} / {globalReview.toFixed(2)})
      </label>
      {override && (
        <>
          <div className="slider-row">
            <label htmlFor={matchId}>
              {stageTitle.replace(/^\d+(\.\d+)? · /, '')} Match ≥ {m.toFixed(2)}
            </label>
            <input
              id={matchId}
              type="range"
              min={0.5}
              max={1}
              step={0.01}
              value={m}
              onChange={(e) => onChange({ ...override, match: Number(e.target.value) })}
            />
          </div>
          <div className="slider-row">
            <label htmlFor={reviewId}>
              {stageTitle.replace(/^\d+(\.\d+)? · /, '')} Review ≥ {r.toFixed(2)}
            </label>
            <input
              id={reviewId}
              type="range"
              min={0.5}
              max={1}
              step={0.01}
              value={r}
              onChange={(e) => onChange({ ...override, review: Number(e.target.value) })}
            />
          </div>
          <p className="muted small">
            Pairs from this stage classify against these cutoffs instead of the globals. Lowering
            its Review also lowers the stage&apos;s candidate floor, surfacing more borderline pairs
            for review.
          </p>
        </>
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Advanced matching options — collapsible panel for the Phase-1
// feature flags (phone matching, phonetic blocking, rare-name weighting,
// domain/phone corroboration guard — default ON) plus the opt-in semantic
// stage (default OFF; needs the optional sentence-transformers install).
// ════════════════════════════════════════════════════════════════════════

const ADVANCED_TOGGLES: {
  key:
    | 'use_phone'
    | 'use_phonetic_blocking'
    | 'splink_tf_adjustments'
    | 'deterministic_corroboration'
    | 'use_embedding'
  title: string
  desc: string
}[] = [
  {
    key: 'use_phone',
    title: 'Match on phone numbers',
    desc: 'Exact-match normalized phone numbers as a strong identifier (needs a mapped phone column).',
  },
  {
    key: 'use_phonetic_blocking',
    title: 'Phonetic blocking',
    desc: 'Also compare names that sound alike (Metaphone), catching typos in the first letters.',
  },
  {
    key: 'splink_tf_adjustments',
    title: 'Rare-name weighting',
    desc: 'Agreement on a rare name/city counts for more than agreement on a common one.',
  },
  {
    key: 'deterministic_corroboration',
    title: 'Guard shared domains/phones',
    desc: 'A shared website or phone with totally different names goes to review instead of auto-matching.',
  },
  {
    key: 'use_embedding',
    title: 'Semantic matching (embeddings)',
    desc: 'Catch names no string metric can — "IBM" ↔ "International Business Machines" — with a small local model. Suggestions go to review only, never auto-match. Needs the optional sentence-transformers install; skipped (with a warning in the run log) if it isn\'t present.',
  },
]

function AdvancedPanel({
  config,
  setConfig,
}: {
  config: SessionConfig
  setConfig: (c: SessionConfig) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="detected advanced">
      <div className="detected-head">
        <div>
          <strong>Advanced matching options</strong>{' '}
          <span className="muted small">
            {ADVANCED_TOGGLES.filter((t) => Boolean(config[t.key])).length}/
            {ADVANCED_TOGGLES.length} enabled
          </span>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
          {open ? 'Done' : 'Adjust'}
        </button>
      </div>
      {open && (
        <div className="detected-body">
          <div className="stage-list">
            {ADVANCED_TOGGLES.map((t) => (
              <label key={t.key} className={`stage ${config[t.key] ? 'on' : ''}`}>
                <input
                  type="checkbox"
                  checked={Boolean(config[t.key])}
                  onChange={() => setConfig({ ...config, [t.key]: !config[t.key] })}
                  aria-label={t.title}
                />
                <div>
                  <div className="stage-title">{t.title}</div>
                  <div className="muted">{t.desc}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Detected fields (dedupe only) — one-line summary of the auto-
// guessed column mapping with an expandable editor to correct any guess.
// ════════════════════════════════════════════════════════════════════════

function DetectedFields({
  fields,
  upload,
  mapping,
  setMapping,
  open,
  onToggle,
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
