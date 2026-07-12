import { useCallback, useEffect, useRef, useState } from 'react'
import {
  downloadUrl,
  getEvaluation,
  getResults,
  postBulkDecisions,
  postDecision,
  reclassify,
  uploadGroundTruth,
  type Decision,
  type Evaluation,
  type MatchPair,
  type ResultsResponse,
} from '../api'

interface Props {
  sessionId: string
  onReset: () => void
}

const PAGE_SIZE = 25
const FILTERS = [
  { key: 'pairs', label: 'All pairs', count: 'pairs' },
  { key: 'match', label: 'Matches', count: 'match' },
  { key: 'review', label: 'Review', count: 'review' },
  { key: 'dismissed', label: 'Dismissed', count: 'dismissed' },
  { key: 'unique_a', label: 'Unique in A', count: 'unique_a' },
  { key: 'unique_b', label: 'Unique in B', count: 'unique_b' },
]

export function ResultsStep({ sessionId, onReset }: Props) {
  const [data, setData] = useState<ResultsResponse | null>(null)
  const [evalu, setEvalu] = useState<Evaluation | null>(null)
  const [filter, setFilter] = useState('pairs')
  const [page, setPage] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [focused, setFocused] = useState(0)

  // Accuracy vs ground truth (uploaded per session, or the repo's developer file).
  // Refreshes after decisions since confirming/dismissing changes the match set.
  const refreshEval = useCallback(() => {
    getEvaluation(sessionId)
      .then(setEvalu)
      .catch(() => setEvalu(null))
  }, [sessionId])

  const load = useCallback(
    async (p = page) => {
      setError(null)
      try {
        setData(await getResults(sessionId, { matchClass: filter, page: p, pageSize: PAGE_SIZE }))
        refreshEval()
      } catch (e) {
        setError((e as Error).message)
      }
    },
    [sessionId, filter, page, refreshEval],
  )

  useEffect(() => {
    load(page)
    setFocused(0)
  }, [load, page])

  async function decide(p: MatchPair, decision: Decision) {
    try {
      await postDecision(sessionId, p.idx_a, p.idx_b, decision)
      // The item usually leaves the current filter; step back a page if it emptied.
      const remaining = (data?.pairs.length ?? 1) - 1
      if (remaining === 0 && page > 0) setPage((x) => x - 1)
      else await load(page)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function decideAllInView(decision: Decision) {
    if (!data || data.pairs.length === 0) return
    const label = decision === 'confirmed' ? 'Confirm' : 'Dismiss'
    if (!window.confirm(`${label} all ${data.total} pair(s) in this view?`)) return
    try {
      // The current filter's full set, not just this page: walk pages server-side by
      // sending every pair currently classified in this filter.
      const all: { idx_a: number; idx_b: number }[] = []
      let p = 0
      for (;;) {
        const chunk = await getResults(sessionId, {
          matchClass: filter,
          page: p,
          pageSize: 200,
        })
        all.push(...chunk.pairs.map((x) => ({ idx_a: x.idx_a, idx_b: x.idx_b })))
        if ((p + 1) * 200 >= chunk.total) break
        p += 1
      }
      const res = await postBulkDecisions(sessionId, all, decision)
      setNotice(`${label}ed ${res.counts.bulk_applied ?? all.length} pairs`)
      setPage(0)
      await load(0)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Keyboard review: arrows/j/k navigate, y confirm, n dismiss, u undo.
  const dataRef = useRef(data)
  dataRef.current = data
  const focusedRef = useRef(focused)
  focusedRef.current = focused
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      const pairs = dataRef.current?.pairs ?? []
      if (pairs.length === 0) return
      const i = Math.min(focusedRef.current, pairs.length - 1)
      const pair = pairs[i]
      if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault()
        setFocused(Math.min(i + 1, pairs.length - 1))
      } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault()
        setFocused(Math.max(i - 1, 0))
      } else if (e.key === 'y' && pair.match_class === 'review') {
        decide(pair, 'confirmed')
      } else if (e.key === 'n' && pair.match_class === 'review') {
        decide(pair, 'dismissed')
      } else if (
        e.key === 'u' &&
        (pair.decision === 'confirmed' || pair.match_class === 'dismissed')
      ) {
        decide(pair, 'pending')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, filter, page])

  const c = data?.counts ?? {}
  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  return (
    <div>
      <div className="results-head">
        <h2>Results</h2>
        <div className="head-right">
          {evalu?.available && (
            <EvalBox
              e={evalu}
              onApplySuggestion={
                evalu.suggested_match_threshold != null
                  ? async () => {
                      try {
                        await reclassify(sessionId, evalu.suggested_match_threshold!, 0.75)
                        setNotice(
                          `Applied suggested match threshold ${evalu.suggested_match_threshold}`,
                        )
                        await load(0)
                      } catch (e) {
                        setError((e as Error).message)
                      }
                    }
                  : undefined
              }
            />
          )}
          <GroundTruthUpload
            sessionId={sessionId}
            onUploaded={(ev) => {
              setEvalu(ev)
              setNotice('Ground truth uploaded — accuracy panel updated')
            }}
            onError={setError}
          />
          <a className="btn btn-primary" href={downloadUrl(sessionId)}>
            ⬇ Download CSV
          </a>
        </div>
      </div>

      {(c.review ?? 0) > 0 && (
        <p className="muted small review-hint">
          {c.review} pair{c.review === 1 ? '' : 's'} need review — open the <strong>Review</strong>{' '}
          tab to confirm or dismiss each one (keyboard: <kbd>↑↓</kbd> navigate, <kbd>y</kbd>{' '}
          confirm, <kbd>n</kbd> dismiss, <kbd>u</kbd> undo). Confirmed matches are included in the
          downloaded dataset.
        </p>
      )}

      <div className="reconcile">
        <ReconcileRow
          title="File A"
          total={c.records_a}
          segments={[
            { label: 'Matched', value: c.match, tone: 'match' },
            { label: 'Review', value: c.review, tone: 'review' },
            { label: 'Unique', value: c.unique_a, tone: 'unique' },
          ]}
        />
        <ReconcileRow
          title="File B"
          total={c.records_b}
          segments={[
            { label: 'Matched', value: c.matched_b, tone: 'match' },
            { label: 'Unique', value: c.unique_b, tone: 'unique' },
          ]}
        />
      </div>

      <ThresholdPanel
        sessionId={sessionId}
        onApplied={async (msg) => {
          setNotice(msg)
          setPage(0)
          await load(0)
        }}
        onError={setError}
      />

      <div className="filter-tabs">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`tab ${filter === f.key ? 'active' : ''}`}
            onClick={() => {
              setFilter(f.key)
              setPage(0)
            }}
          >
            {f.label}
            <span className="tab-count">{c[f.count] ?? 0}</span>
          </button>
        ))}
      </div>

      {filter === 'review' && (data?.total ?? 0) > 0 && (
        <div className="bulk-bar">
          <span className="muted small">Bulk actions on all {data?.total} in this view:</span>
          <button className="btn btn-sm btn-confirm" onClick={() => decideAllInView('confirmed')}>
            ✓ Confirm all
          </button>
          <button className="btn btn-sm btn-dismiss" onClick={() => decideAllInView('dismissed')}>
            ✕ Dismiss all
          </button>
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {notice && (
        <div className="notice" onClick={() => setNotice(null)}>
          {notice}
        </div>
      )}
      {data && data.pairs.length === 0 && <p className="muted">No records in this view.</p>}

      <div className="pair-list">
        {data?.pairs.map((p, i) => (
          <PairCard
            key={`${p.idx_a}-${p.idx_b}-${i}`}
            pair={p}
            focused={i === focused}
            onFocus={() => setFocused(i)}
            onDecide={decide}
          />
        ))}
      </div>

      {totalPages > 1 && (
        <div className="pager">
          <button
            className="btn btn-ghost"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            ← Prev
          </button>
          <span className="muted">
            Page {page + 1} of {totalPages}
          </span>
          <button
            className="btn btn-ghost"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            Next →
          </button>
        </div>
      )}

      <div className="actions">
        <button className="btn btn-ghost" onClick={onReset}>
          ↺ Start over
        </button>
        <span />
      </div>
    </div>
  )
}

// Adjust thresholds and re-classify stored scores without re-running the pipeline.
function ThresholdPanel({
  sessionId,
  onApplied,
  onError,
}: {
  sessionId: string
  onApplied: (msg: string) => void
  onError: (msg: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [match, setMatch] = useState(0.9)
  const [review, setReview] = useState(0.75)
  const [busy, setBusy] = useState(false)

  async function apply() {
    setBusy(true)
    try {
      const res = await reclassify(sessionId, match, review)
      onApplied(`Re-classified: ${res.match} matches, ${res.review} to review`)
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="detected thresholds-panel">
      <div className="detected-head">
        <div>
          <strong>Thresholds</strong>{' '}
          <span className="muted small">
            re-classify stored scores without re-running the match
          </span>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
          {open ? 'Close' : 'Adjust'}
        </button>
      </div>
      {open && (
        <div className="detected-body">
          <div className="slider-row">
            <label>Match ≥ {match.toFixed(2)}</label>
            <input
              type="range"
              min={0.5}
              max={1}
              step={0.01}
              value={match}
              onChange={(e) => setMatch(Number(e.target.value))}
            />
          </div>
          <div className="slider-row">
            <label>Review ≥ {review.toFixed(2)}</label>
            <input
              type="range"
              min={0.5}
              max={1}
              step={0.01}
              value={review}
              onChange={(e) => setReview(Number(e.target.value))}
            />
          </div>
          <button
            className="btn btn-sm btn-primary"
            disabled={busy || review > match}
            onClick={apply}
          >
            {busy ? 'Applying…' : 'Apply & re-classify'}
          </button>
          {review > match && <span className="error-inline"> review must be ≤ match</span>}
        </div>
      )}
    </div>
  )
}

function GroundTruthUpload({
  sessionId,
  onUploaded,
  onError,
}: {
  sessionId: string
  onUploaded: (e: Evaluation) => void
  onError: (msg: string) => void
}) {
  const [busy, setBusy] = useState(false)
  return (
    <label
      className="btn btn-ghost btn-sm"
      title="Upload a CSV of known-correct matches (columns: file_a_name, file_b_name) to measure precision/recall"
    >
      {busy ? 'Scoring…' : '🧪 Upload ground truth'}
      <input
        type="file"
        accept=".csv,.xlsx"
        hidden
        onChange={async (e) => {
          const f = e.target.files?.[0]
          if (!f) return
          setBusy(true)
          try {
            onUploaded(await uploadGroundTruth(sessionId, f))
          } catch (err) {
            onError((err as Error).message)
          } finally {
            setBusy(false)
            e.target.value = ''
          }
        }}
      />
    </label>
  )
}

function PairCard({
  pair,
  focused,
  onFocus,
  onDecide,
}: {
  pair: MatchPair
  focused: boolean
  onFocus: () => void
  onDecide: (p: MatchPair, d: Decision) => void
}) {
  const hasA = Object.keys(pair.record_a).length > 0
  const hasB = Object.keys(pair.record_b).length > 0
  const twoSided = hasA && hasB
  const keys = Array.from(new Set([...Object.keys(pair.record_a), ...Object.keys(pair.record_b)]))
  const cls = pair.match_class
  const isReview = cls === 'review'
  const isDismissed = cls === 'dismissed'
  const isConfirmed = cls === 'match' && pair.decision === 'confirmed'
  const adjustments = pair.adjustments ? pair.adjustments.split(',').filter(Boolean) : []

  return (
    <div className={`pair-card ${cls} ${focused ? 'focused' : ''}`} onClick={onFocus}>
      <div className="pair-meta">
        <span className={`badge ${cls}`}>{LABEL[cls] ?? cls}</span>
        {isConfirmed && <span className="badge confirmed">✓ confirmed</span>}
        {twoSided &&
          (() => {
            const r = matchReason(pair)
            return (
              <span className="reason" title={r.detail}>
                <span className="reason-icon">{r.icon}</span> {r.label}
              </span>
            )
          })()}
        {adjustments.map((a) => (
          <span
            key={a}
            className={`adj-chip ${a === 'addr_conflict' ? 'bad' : 'good'}`}
            title={ADJ_TITLES[a] ?? a}
          >
            {ADJ_LABELS[a] ?? a}
          </span>
        ))}

        <span className="pair-actions">
          {isReview && (
            <>
              <button
                className="btn btn-sm btn-confirm"
                onClick={() => onDecide(pair, 'confirmed')}
              >
                ✓ Confirm match
              </button>
              <button
                className="btn btn-sm btn-dismiss"
                onClick={() => onDecide(pair, 'dismissed')}
              >
                ✕ Dismiss
              </button>
            </>
          )}
          {(isConfirmed || isDismissed) && (
            <button className="btn btn-sm btn-ghost" onClick={() => onDecide(pair, 'pending')}>
              ↺ Undo
            </button>
          )}
        </span>
      </div>
      <table className="pair-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>File A</th>
            <th>File B</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const a = pair.record_a[k] ?? ''
            const b = pair.record_b[k] ?? ''
            const diff = twoSided && a.trim().toLowerCase() !== b.trim().toLowerCase()
            return (
              <tr key={k} className={diff ? 'diff' : ''}>
                <td className="k">{k}</td>
                <td>{hasA ? a : <span className="muted">—</span>}</td>
                <td>{hasB ? b : <span className="muted">—</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const LABEL: Record<string, string> = {
  match: 'match',
  review: 'review',
  dismissed: 'dismissed',
  unique_a: 'unique · A',
  unique_b: 'unique · B',
}

// Compact provenance chips: which corroboration signals moved the score.
const ADJ_LABELS: Record<string, string> = {
  addr_agree: '+addr',
  addr_conflict: '−addr',
  same_city: '+city',
  same_postcode: '+zip',
  same_state: '+state',
  same_country: '+country',
}
const ADJ_TITLES: Record<string, string> = {
  addr_agree: 'Addresses agree (+0.05)',
  addr_conflict: 'Addresses clearly differ (−0.18) — demoted toward review',
  same_city: 'Same city (+0.03)',
  same_postcode: 'Same postcode (+0.03)',
  same_state: 'Same state (+0.03)',
  same_country: 'Same country (+0.03)',
}

// A human-readable explanation of why the cascade linked a pair, derived from the
// stage/rule that produced it (plus the score for the fuzzy/probabilistic stages).
function matchReason(pair: MatchPair): { icon: string; label: string; detail: string } {
  const pct = Math.round(pair.score * 100)
  const confirmed = pair.decision === 'confirmed'
  const base = (() => {
    switch (pair.rule) {
      case 'registration_id':
        return {
          icon: '🔑',
          label: 'Exact registration / tax ID',
          detail:
            'Both records share the same registration or tax identifier after normalization — a near-certain match.',
        }
      case 'domain':
      case 'domain+name_ok':
        return {
          icon: '🌐',
          label: 'Same website domain',
          detail:
            'Both records resolve to the same web domain (and the names are compatible) — a near-certain match.',
        }
      case 'domain_name_conflict':
        return {
          icon: '🌐',
          label: 'Same domain, names differ',
          detail:
            'Both records share a web domain but the company names look unrelated — could be subsidiaries or portfolio companies sharing a site. Review before accepting.',
        }
      case 'phone':
      case 'phone+name_ok':
        return {
          icon: '📞',
          label: 'Same phone number',
          detail:
            'Both records share the same normalized phone number (and the names are compatible).',
        }
      case 'phone_name_conflict':
        return {
          icon: '📞',
          label: 'Same phone, names differ',
          detail:
            'Both records share a phone number but the names look unrelated — could be a shared switchboard. Review before accepting.',
        }
      case 'splink':
        return {
          icon: '◈',
          label: `Probabilistic field match · ${pct}%`,
          detail: `Splink's probabilistic model scored agreement across name, address and other fields at ${pct}% match probability.`,
        }
      case 'name_token_set':
        return {
          icon: '≈',
          label: `Similar company name · ${pct}%`,
          detail: `Company names are ${pct}% similar after normalization (legal suffixes stripped, abbreviations expanded, tokens compared order-independently).`,
        }
      default:
        return { icon: '•', label: pair.stage || 'matched', detail: '' }
    }
  })()
  if (confirmed) {
    return {
      ...base,
      label: `${base.label} · you confirmed`,
      detail: `${base.detail} You manually confirmed this pair as a match.`,
    }
  }
  return base
}

// Accuracy panel comparing the match set to ground truth (uploaded or repo file).
function EvalBox({ e, onApplySuggestion }: { e: Evaluation; onApplySuggestion?: () => void }) {
  if (!e.applicable) {
    return (
      <div className="eval-box" title={e.reason ?? ''}>
        <div className="eval-title">🧪 ground truth</div>
        <div className="eval-na">no overlap</div>
      </div>
    )
  }
  const pct = (x: number) => `${Math.round(x * 100)}%`
  const suggestionBeatsCurrent =
    e.suggested_match_threshold != null && (e.suggested_f1 ?? 0) > e.f1 + 0.0001
  return (
    <div
      className="eval-box"
      title={`Confirmed matches scored against ${e.gt_source === 'uploaded' ? 'your uploaded ground truth' : 'ground_truth_matches.csv'}.\nF1 ${pct(e.f1)} · precision ${pct(e.precision)} · recall ${pct(e.recall)}\n${e.tp} correct, ${e.fp} false, ${e.fn} missed of ${e.ground_truth_total} known matches.`}
    >
      <div className="eval-title">🧪 vs ground truth</div>
      <div className="eval-score">
        {pct(e.f1)} <span className="eval-metric">F1</span>
      </div>
      <div className="eval-sub">
        <span>P {pct(e.precision)}</span>
        <span>R {pct(e.recall)}</span>
      </div>
      <div className="eval-counts">
        <span className="ok">✓{e.tp}</span>
        <span className="bad">✗{e.fp}</span>
        <span className="miss">⌀{e.fn}</span>
      </div>
      {suggestionBeatsCurrent && onApplySuggestion && (
        <button
          className="btn btn-sm btn-ghost eval-suggest"
          title={`Setting the match threshold to ${e.suggested_match_threshold} would give F1 ${pct(e.suggested_f1 ?? 0)} (P ${pct(e.suggested_precision ?? 0)} / R ${pct(e.suggested_recall ?? 0)})`}
          onClick={onApplySuggestion}
        >
          Apply suggested {e.suggested_match_threshold}
        </button>
      )}
    </div>
  )
}

interface Segment {
  label: string
  value?: number
  tone: string
}

function ReconcileRow({
  title,
  total,
  segments,
}: {
  title: string
  total?: number
  segments: Segment[]
}) {
  const t = total ?? 0
  return (
    <div className="reconcile-row">
      <div className="reconcile-head">
        <span className="reconcile-title">{title}</span>
        <span className="reconcile-total">{t.toLocaleString()} rows</span>
      </div>
      <div className="reconcile-bar">
        {segments.map((s) => {
          const v = s.value ?? 0
          const pct = t > 0 ? (v / t) * 100 : 0
          return v > 0 ? (
            <div
              key={s.label}
              className={`seg ${s.tone}`}
              style={{ width: `${pct}%` }}
              title={`${s.label}: ${v}`}
            />
          ) : null
        })}
      </div>
      <div className="reconcile-legend">
        {segments.map((s) => (
          <span key={s.label} className={`legend ${s.tone}`}>
            <i className="swatch" /> {s.label} <strong>{(s.value ?? 0).toLocaleString()}</strong>
          </span>
        ))}
      </div>
    </div>
  )
}
