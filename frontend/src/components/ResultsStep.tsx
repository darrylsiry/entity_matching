// Results screen (link mode) — the review workbench. Shows reconciliation bars,
// filter tabs, and pair cards with full provenance; supports single/bulk
// confirm-dismiss, keyboard review (↑↓/jk + y/n/u), instant threshold
// re-classification, ground-truth evaluation, and CSV export.
import { useCallback, useEffect, useId, useRef, useState } from 'react'
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Main screen — loads the current filter's page of pairs (plus the
// evaluation panel), applies decisions, walks all pages for bulk actions,
// and wires the keyboard shortcuts.
// ════════════════════════════════════════════════════════════════════════

export function ResultsStep({ sessionId, onReset }: Props) {
  const [data, setData] = useState<ResultsResponse | null>(null)
  const [evalu, setEvalu] = useState<Evaluation | null>(null)
  const [filter, setFilter] = useState('pairs')
  const [page, setPage] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [focused, setFocused] = useState(0)
  // Last single decision, surfaced as a transient toast with an Undo button.
  const [toast, setToast] = useState<{ pair: MatchPair; decision: Decision } | null>(null)
  const toastTimer = useRef<number | null>(null)
  // Bulk actions: two-step inline confirm + busy flag so double-clicks can't
  // fire duplicate bulk posts.
  const [bulkArming, setBulkArming] = useState<Decision | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)

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

  function showToast(pair: MatchPair, decision: Decision) {
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    setToast({ pair, decision })
    toastTimer.current = window.setTimeout(() => setToast(null), 7000)
  }

  async function decide(p: MatchPair, decision: Decision) {
    try {
      await postDecision(sessionId, p.idx_a, p.idx_b, decision)
      // The item usually leaves the current filter; step back a page if it emptied.
      // (Read through the ref — `data` from the closure can be stale during rapid
      // keyboard review.)
      const remaining = (dataRef.current?.pairs.length ?? 1) - 1
      if (remaining === 0 && page > 0) setPage((x) => x - 1)
      else await load(page)
      if (decision === 'pending') setToast(null)
      else showToast(p, decision)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function decideAllInView(decision: Decision) {
    if (!data || data.pairs.length === 0 || bulkBusy) return
    const label = decision === 'confirmed' ? 'Confirm' : 'Dismiss'
    setBulkArming(null)
    setBulkBusy(true)
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
      const applied = res.counts.bulk_applied ?? all.length
      setNotice(`${label}ed ${applied} ${applied === 1 ? 'pair' : 'pairs'}`)
      setPage(0)
      await load(0)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBulkBusy(false)
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
            <span aria-hidden="true">⬇</span> Download CSV
          </a>
        </div>
      </div>

      {(c.review ?? 0) > 0 && (
        <p className="muted small review-hint">
          {c.review} pair{c.review === 1 ? '' : 's'} need review — open the <strong>Review</strong>{' '}
          tab to confirm or dismiss each one (keyboard: <kbd>↑↓</kbd> or <kbd>j</kbd>/<kbd>k</kbd>{' '}
          navigate, <kbd>y</kbd> confirm, <kbd>n</kbd> dismiss, <kbd>u</kbd> undo). Confirmed
          matches are included in the downloaded dataset.
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
            aria-pressed={filter === f.key}
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
          {bulkBusy ? (
            <span className="muted small" role="status">
              Applying to all {data?.total} {data?.total === 1 ? 'pair' : 'pairs'}…
            </span>
          ) : bulkArming ? (
            <>
              <span className="small">
                {bulkArming === 'confirmed' ? 'Confirm' : 'Dismiss'} all {data?.total}{' '}
                {data?.total === 1 ? 'pair' : 'pairs'} in this view?
              </span>
              <button
                className={`btn btn-sm ${bulkArming === 'confirmed' ? 'btn-confirm' : 'btn-dismiss'}`}
                onClick={() => decideAllInView(bulkArming)}
              >
                Yes, {bulkArming === 'confirmed' ? 'confirm' : 'dismiss'} all
              </button>
              <button className="btn btn-sm btn-ghost" onClick={() => setBulkArming(null)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <span className="muted small">Bulk actions on all {data?.total} in this view:</span>
              <button className="btn btn-sm btn-confirm" onClick={() => setBulkArming('confirmed')}>
                <span aria-hidden="true">✓</span> Confirm all
              </button>
              <button className="btn btn-sm btn-dismiss" onClick={() => setBulkArming('dismissed')}>
                <span aria-hidden="true">✕</span> Dismiss all
              </button>
            </>
          )}
        </div>
      )}

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button
            className="notice-close"
            aria-label="Dismiss notice"
            onClick={() => setNotice(null)}
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}
      {data && data.pairs.length === 0 && <p className="muted">No records in this view.</p>}

      <div className="pair-list">
        {data?.pairs.map((p, i) => (
          <PairCard
            key={`${p.idx_a}-${p.idx_b}`}
            pair={p}
            focused={i === focused}
            onFocus={() => setFocused(i)}
            onDecide={decide}
          />
        ))}
      </div>

      {toast && (
        <div className="toast" role="status">
          <span>
            {toast.decision === 'confirmed' ? 'Confirmed' : 'Dismissed'}{' '}
            <strong>
              {toast.pair.record_a['company_name'] ??
                Object.values(toast.pair.record_a)[0] ??
                `row ${toast.pair.idx_a}`}
            </strong>{' '}
            ↔{' '}
            <strong>
              {toast.pair.record_b['company_name'] ??
                Object.values(toast.pair.record_b)[0] ??
                `row ${toast.pair.idx_b}`}
            </strong>
          </span>
          <button className="btn btn-sm btn-ghost" onClick={() => decide(toast.pair, 'pending')}>
            <span aria-hidden="true">↺</span> Undo
          </button>
          <button className="notice-close" aria-label="Dismiss" onClick={() => setToast(null)}>
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}

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
          <span aria-hidden="true">↺</span> Start over
        </button>
        <span />
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Threshold panel — sliders + "Apply & re-classify", which re-bands
// the stored scores on the server without re-running the matching pipeline.
// ════════════════════════════════════════════════════════════════════════

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
  const matchSliderId = useId()
  const reviewSliderId = useId()

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
            <label htmlFor={matchSliderId}>Match ≥ {match.toFixed(2)}</label>
            <input
              id={matchSliderId}
              type="range"
              min={0.5}
              max={1}
              step={0.01}
              value={match}
              onChange={(e) => setMatch(Number(e.target.value))}
            />
          </div>
          <div className="slider-row">
            <label htmlFor={reviewSliderId}>Review ≥ {review.toFixed(2)}</label>
            <input
              id={reviewSliderId}
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Ground-truth upload — a labeled sample (file_a_name,file_b_name)
// the session is scored against; refreshes the evaluation panel on success.
// ════════════════════════════════════════════════════════════════════════

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
      {busy ? (
        'Scoring…'
      ) : (
        <>
          <span aria-hidden="true">🧪</span> Upload ground truth
        </>
      )}
      <input
        type="file"
        accept=".csv,.xlsx"
        className="sr-only"
        aria-label="Upload ground truth CSV of known-correct matches (columns: file_a_name, file_b_name)"
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Pair card — one A↔B pair: class badge, match reason + adjustment
// chips (the provenance), confirm/dismiss/undo buttons, and a side-by-side
// field table with differing values highlighted.
// ════════════════════════════════════════════════════════════════════════

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
  // Hide fields that are empty on both sides — they carry no signal and halve
  // how many pairs fit on screen during review.
  const keys = Array.from(
    new Set([...Object.keys(pair.record_a), ...Object.keys(pair.record_b)]),
  ).filter((k) => (pair.record_a[k] ?? '').trim() !== '' || (pair.record_b[k] ?? '').trim() !== '')
  const cls = pair.match_class
  const isReview = cls === 'review'
  const isDismissed = cls === 'dismissed'
  const isConfirmed = cls === 'match' && pair.decision === 'confirmed'
  const adjustments = pair.adjustments ? pair.adjustments.split(',').filter(Boolean) : []
  const [showDetail, setShowDetail] = useState(false)
  const detailId = useId()
  const cardRef = useRef<HTMLDivElement | null>(null)

  // Keep the keyboard-focused card in view while j/k walking the queue.
  useEffect(() => {
    if (focused) cardRef.current?.scrollIntoView({ block: 'nearest' })
  }, [focused])

  const reason = twoSided ? matchReason(pair) : null

  return (
    <div ref={cardRef} className={`pair-card ${cls} ${focused ? 'focused' : ''}`} onClick={onFocus}>
      <div className="pair-meta">
        <span className={`badge ${cls}`}>{LABEL[cls] ?? cls}</span>
        {isConfirmed && (
          <span className="badge confirmed">
            <span aria-hidden="true">✓</span> confirmed
          </span>
        )}
        {reason && (
          <button
            className="reason"
            aria-expanded={showDetail}
            aria-controls={detailId}
            title={showDetail ? undefined : 'Why did this match? Click for the explanation.'}
            onClick={(e) => {
              e.stopPropagation()
              setShowDetail((v) => !v)
            }}
          >
            <span className="reason-icon" aria-hidden="true">
              {reason.icon}
            </span>{' '}
            {reason.label}
          </button>
        )}
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
                <span aria-hidden="true">✓</span> Confirm match
              </button>
              <button
                className="btn btn-sm btn-dismiss"
                onClick={() => onDecide(pair, 'dismissed')}
              >
                <span aria-hidden="true">✕</span> Dismiss
              </button>
            </>
          )}
          {(isConfirmed || isDismissed) && (
            <button className="btn btn-sm btn-ghost" onClick={() => onDecide(pair, 'pending')}>
              <span aria-hidden="true">↺</span> Undo
            </button>
          )}
        </span>
      </div>
      {reason && showDetail && (
        <div id={detailId} className="reason-detail">
          <p>{reason.detail}</p>
          {adjustments.length > 0 && (
            <ul>
              {adjustments.map((a) => (
                <li key={a}>{ADJ_TITLES[a] ?? a}</li>
              ))}
            </ul>
          )}
        </div>
      )}
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Provenance display constants + matchReason()
// Human-readable badge labels, chip labels/tooltips for each corroboration
// signal, and the per-rule explanation of WHY the cascade linked a pair.
// ════════════════════════════════════════════════════════════════════════

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
// ════════════════════════════════════════════════════════════════════════
// SECTION: Evaluation box — P/R/F1 vs ground truth, plus an "apply suggested
// threshold" button when the swept best-F1 point beats the current setting.
// ════════════════════════════════════════════════════════════════════════

function EvalBox({ e, onApplySuggestion }: { e: Evaluation; onApplySuggestion?: () => void }) {
  if (!e.applicable) {
    return (
      <div className="eval-box" title={e.reason ?? ''}>
        <div className="eval-title">
          <span aria-hidden="true">🧪</span> ground truth
        </div>
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
      <div className="eval-title">
        <span aria-hidden="true">🧪</span> vs ground truth
      </div>
      <div className="eval-score">
        {pct(e.f1)} <span className="eval-metric">F1</span>
      </div>
      <div className="eval-sub">
        <span>P {pct(e.precision)}</span>
        <span>R {pct(e.recall)}</span>
      </div>
      <div className="eval-counts">
        <span className="ok">
          <span aria-hidden="true">✓</span>
          {e.tp}
          <span className="sr-only"> correct</span>
        </span>
        <span className="bad">
          <span aria-hidden="true">✗</span>
          {e.fp}
          <span className="sr-only"> false positives</span>
        </span>
        <span className="miss">
          <span aria-hidden="true">⌀</span>
          {e.fn}
          <span className="sr-only"> missed of {e.ground_truth_total} known matches</span>
        </span>
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

// ════════════════════════════════════════════════════════════════════════
// SECTION: Reconciliation bars — per-file stacked bar proving the categories
// add up: matched + review + unique == total rows.
// ════════════════════════════════════════════════════════════════════════

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
      {/* Decorative — the legend below carries the same numbers as text. */}
      <div className="reconcile-bar" aria-hidden="true">
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
