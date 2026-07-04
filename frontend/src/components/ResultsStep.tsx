import { useCallback, useEffect, useState } from 'react'
import {
  downloadUrl,
  getEvaluation,
  getResults,
  postDecision,
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

  // Debug: accuracy vs the repo's ground-truth file. Refreshes after decisions
  // since confirming/dismissing changes the match set. Silently ignored if absent.
  const refreshEval = useCallback(() => {
    getEvaluation(sessionId).then(setEvalu).catch(() => setEvalu(null))
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

  const c = data?.counts ?? {}
  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  return (
    <div>
      <div className="results-head">
        <h2>Results</h2>
        <div className="head-right">
          {evalu?.available && <EvalBox e={evalu} />}
          <a className="btn btn-primary" href={downloadUrl(sessionId)}>
            ⬇ Download CSV
          </a>
        </div>
      </div>

      {(c.review ?? 0) > 0 && (
        <p className="muted small review-hint">
          {c.review} pair{c.review === 1 ? '' : 's'} need review — open the{' '}
          <strong>Review</strong> tab to confirm or dismiss each one. Confirmed matches
          are included in the downloaded dataset.
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

      {error && <div className="error">{error}</div>}
      {data && data.pairs.length === 0 && <p className="muted">No records in this view.</p>}

      <div className="pair-list">
        {data?.pairs.map((p, i) => (
          <PairCard key={`${p.idx_a}-${p.idx_b}-${i}`} pair={p} onDecide={decide} />
        ))}
      </div>

      {totalPages > 1 && (
        <div className="pager">
          <button className="btn btn-ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
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

function PairCard({
  pair,
  onDecide,
}: {
  pair: MatchPair
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

  return (
    <div className={`pair-card ${cls}`}>
      <div className="pair-meta">
        <span className={`badge ${cls}`}>{LABEL[cls] ?? cls}</span>
        {isConfirmed && <span className="badge confirmed">✓ confirmed</span>}
        {twoSided && (() => {
          const r = matchReason(pair)
          return (
            <span className="reason" title={r.detail}>
              <span className="reason-icon">{r.icon}</span> {r.label}
            </span>
          )
        })()}

        <span className="pair-actions">
          {isReview && (
            <>
              <button className="btn btn-sm btn-confirm" onClick={() => onDecide(pair, 'confirmed')}>
                ✓ Confirm match
              </button>
              <button className="btn btn-sm btn-dismiss" onClick={() => onDecide(pair, 'dismissed')}>
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
          detail: 'Both records share the same registration or tax identifier after normalization — a near-certain match.',
        }
      case 'domain':
        return {
          icon: '🌐',
          label: 'Same website domain',
          detail: 'Both records resolve to the same web domain after stripping www/paths — a near-certain match.',
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
          detail: `Company names are ${pct}% similar after normalization (legal suffixes stripped, tokens compared order-independently)${pct < 100 ? '; location agreement may have boosted the score' : ''}.`,
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

// Debug-only accuracy panel comparing the match set to the repo's ground-truth file.
function EvalBox({ e }: { e: Evaluation }) {
  if (!e.applicable) {
    return (
      <div className="eval-box" title={e.reason ?? ''}>
        <div className="eval-title">🧪 ground truth</div>
        <div className="eval-na">no overlap</div>
      </div>
    )
  }
  const pct = (x: number) => `${Math.round(x * 100)}%`
  return (
    <div
      className="eval-box"
      title={`Confirmed matches scored against ground_truth_matches.csv.\nF1 ${pct(e.f1)} · precision ${pct(e.precision)} · recall ${pct(e.recall)}\n${e.tp} correct, ${e.fp} false, ${e.fn} missed of ${e.ground_truth_total} known matches.`}
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
    </div>
  )
}

interface Segment {
  label: string
  value?: number
  tone: string
}

function ReconcileRow({ title, total, segments }: { title: string; total?: number; segments: Segment[] }) {
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
            <div key={s.label} className={`seg ${s.tone}`} style={{ width: `${pct}%` }} title={`${s.label}: ${v}`} />
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
