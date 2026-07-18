// Results screen (dedupe mode) — master-ID clusters plus the "possible
// duplicates" review queue. Confirming a candidate merges the two rows into
// one master ID; the CSV export is the original file annotated with
// master_id/cluster_size, reflecting those decisions.
import { useCallback, useEffect, useState } from 'react'
import {
  downloadUrl,
  getCandidates,
  getClusters,
  postDecision,
  type Cluster,
  type Decision,
  type DedupeCandidate,
} from '../api'

interface Props {
  sessionId: string
  onReset: () => void
}

const PAGE_SIZE = 25
// Cluster filters + the possible-duplicates review queue (key "possible").
const TABS = [
  { key: 'duplicates', label: 'Duplicate groups' },
  { key: 'all', label: 'All' },
  { key: 'unique', label: 'Unique' },
  { key: 'possible', label: 'Possible duplicates', count: 'possible_duplicates' },
]
const CAND_FILTERS = [
  { key: 'pending', label: 'To review' },
  { key: 'confirmed', label: 'Confirmed' },
  { key: 'dismissed', label: 'Dismissed' },
]

// ════════════════════════════════════════════════════════════════════════
// SECTION: Main screen — one loader serving two views: cluster tabs
// (duplicates/all/unique) and the possible-duplicates queue (pending/
// confirmed/dismissed). Decisions reload the current page + counts.
// ════════════════════════════════════════════════════════════════════════

export function DedupeResultsStep({ sessionId, onReset }: Props) {
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [candidates, setCandidates] = useState<DedupeCandidate[]>([])
  const [total, setTotal] = useState(0)
  const [tab, setTab] = useState('duplicates')
  const [candFilter, setCandFilter] = useState('pending')
  const [page, setPage] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (p = page) => {
      setError(null)
      try {
        if (tab === 'possible') {
          const r = await getCandidates(sessionId, {
            filter: candFilter,
            page: p,
            pageSize: PAGE_SIZE,
          })
          setCandidates(r.candidates)
          setTotal(r.total)
          setCounts(r.counts)
        } else {
          const r = await getClusters(sessionId, { filter: tab, page: p, pageSize: PAGE_SIZE })
          setClusters(r.clusters)
          setTotal(r.total)
          setCounts(r.counts)
        }
      } catch (e) {
        setError((e as Error).message)
      }
    },
    [sessionId, tab, candFilter, page],
  )

  useEffect(() => {
    load(page)
  }, [load, page])

  async function decide(cand: DedupeCandidate, decision: Decision) {
    try {
      await postDecision(sessionId, cand.idx_a, cand.idx_b, decision)
      const remaining = candidates.length - 1
      if (remaining === 0 && page > 0) setPage((x) => x - 1)
      else await load(page)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const onPossible = tab === 'possible'

  return (
    <div>
      <div className="results-head">
        <h2>Deduplication results</h2>
        <a className="btn btn-primary" href={downloadUrl(sessionId)}>
          <span aria-hidden="true">⬇</span> Download CSV
        </a>
      </div>

      <p className="muted small">
        Every row was assigned a <strong>master ID</strong>; rows sharing an ID are the same
        company. Only confident duplicates are merged automatically — borderline pairs go to{' '}
        <strong>Possible duplicates</strong> for you to confirm or dismiss. The CSV is your original
        file with <code>master_id</code> and <code>cluster_size</code> columns added (reflecting
        your decisions).
      </p>

      {(counts.possible_duplicates ?? 0) > 0 && !onPossible && (
        <p className="muted small review-hint">
          {counts.possible_duplicates} possible duplicate
          {counts.possible_duplicates === 1 ? '' : 's'} to review — open the{' '}
          <strong>Possible duplicates</strong> tab. Confirming one merges the rows into a single
          master ID.
        </p>
      )}

      <div className="summary-row">
        <Chip label="Rows" value={counts.records} />
        <Chip label="Unique entities" value={counts.clusters} tone="match" />
        <Chip label="Duplicate rows" value={counts.duplicate_rows} tone="review" />
        <Chip label="Possible duplicates" value={counts.possible_duplicates} />
      </div>

      <div className="filter-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`}
            aria-pressed={tab === t.key}
            onClick={() => {
              setTab(t.key)
              setPage(0)
            }}
          >
            {t.label}
            {t.count && <span className="tab-count">{counts[t.count] ?? 0}</span>}
          </button>
        ))}
      </div>

      {onPossible && (
        <div className="filter-tabs sub">
          {CAND_FILTERS.map((f) => (
            <button
              key={f.key}
              className={`tab ${candFilter === f.key ? 'active' : ''}`}
              aria-pressed={candFilter === f.key}
              onClick={() => {
                setCandFilter(f.key)
                setPage(0)
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      {onPossible ? (
        <>
          {candidates.length === 0 && <p className="muted">No possible duplicates in this view.</p>}
          <div className="pair-list">
            {candidates.map((cand) => (
              <CandidateCard key={`${cand.idx_a}-${cand.idx_b}`} cand={cand} onDecide={decide} />
            ))}
          </div>
        </>
      ) : (
        <>
          {clusters.length === 0 && <p className="muted">No groups in this view.</p>}
          <div className="pair-list">
            {clusters.map((cl) => (
              <ClusterCard key={cl.master_id} cluster={cl} />
            ))}
          </div>
        </>
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
// SECTION: Candidate card — one possible-duplicate pair with score, decision
// badges, "Same company"/"Not a match"/Undo actions, and a side-by-side diff.
// ════════════════════════════════════════════════════════════════════════

function CandidateCard({
  cand,
  onDecide,
}: {
  cand: DedupeCandidate
  onDecide: (c: DedupeCandidate, d: Decision) => void
}) {
  const keys = Array.from(new Set([...Object.keys(cand.record_a), ...Object.keys(cand.record_b)]))
  const decided = cand.decision === 'confirmed' || cand.decision === 'dismissed'
  return (
    <div className={`pair-card ${cand.decision === 'confirmed' ? 'match' : 'review'}`}>
      <div className="pair-meta">
        <span className="badge review">possible duplicate</span>
        {cand.decision === 'confirmed' && (
          <span className="badge confirmed">
            <span aria-hidden="true">✓</span> merged
          </span>
        )}
        {cand.decision === 'dismissed' && <span className="badge dismissed">dismissed</span>}
        <span className="score">{(cand.score * 100).toFixed(0)}%</span>
        {cand.stage && <span className="stage-tag">{cand.stage}</span>}
        <span className="pair-actions">
          {!decided && (
            <>
              <button
                className="btn btn-sm btn-confirm"
                onClick={() => onDecide(cand, 'confirmed')}
              >
                <span aria-hidden="true">✓</span> Same company
              </button>
              <button
                className="btn btn-sm btn-dismiss"
                onClick={() => onDecide(cand, 'dismissed')}
              >
                <span aria-hidden="true">✕</span> Not a match
              </button>
            </>
          )}
          {decided && (
            <button className="btn btn-sm btn-ghost" onClick={() => onDecide(cand, 'pending')}>
              <span aria-hidden="true">↺</span> Undo
            </button>
          )}
        </span>
      </div>
      <table className="pair-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>row {cand.idx_a}</th>
            <th>row {cand.idx_b}</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const a = cand.record_a[k] ?? ''
            const b = cand.record_b[k] ?? ''
            const diff = a.trim().toLowerCase() !== b.trim().toLowerCase()
            return (
              <tr key={k} className={diff ? 'diff' : ''}>
                <td className="k">{k}</td>
                <td>{a}</td>
                <td>{b}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Cluster card — one master-ID group; member rows as columns with
// differing field values highlighted.
// ════════════════════════════════════════════════════════════════════════

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const dup = cluster.size > 1
  const keys = Array.from(new Set(cluster.members.flatMap((m) => Object.keys(m.record))))
  return (
    <div className={`pair-card ${dup ? 'review' : 'unique_a'}`}>
      <div className="pair-meta">
        <span className="badge master">master&nbsp;#{cluster.master_id}</span>
        <span className={`badge ${dup ? 'review' : 'unique_a'}`}>
          {dup ? `${cluster.size} rows` : 'unique'}
        </span>
      </div>
      <table className="pair-table">
        <thead>
          <tr>
            <th>Field</th>
            {cluster.members.map((m) => (
              <th key={m.row_idx}>row {m.row_idx}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const vals = cluster.members.map((m) => m.record[k] ?? '')
            const distinct = new Set(vals.map((v) => v.trim().toLowerCase()))
            const diff = dup && distinct.size > 1
            return (
              <tr key={k} className={diff ? 'diff' : ''}>
                <td className="k">{k}</td>
                {vals.map((v, i) => (
                  <td key={i}>{v}</td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Chip({ label, value, tone }: { label: string; value?: number; tone?: string }) {
  return (
    <div className={`chip ${tone ?? ''}`}>
      <span className="chip-value">{(value ?? 0).toLocaleString()}</span>
      <span className="chip-label">{label}</span>
    </div>
  )
}
