import { useEffect, useState } from 'react'
import { downloadUrl, getResults, type MatchPair, type ResultsResponse } from '../api'

interface Props {
  sessionId: string
  onReset: () => void
}

const PAGE_SIZE = 25
const FILTERS = [
  { key: 'pairs', label: 'All pairs' },
  { key: 'match', label: 'Matches' },
  { key: 'review', label: 'Review' },
  { key: 'unique_a', label: 'Unique in A' },
  { key: 'unique_b', label: 'Unique in B' },
]

const CLASS_LABEL: Record<string, string> = {
  match: 'match',
  review: 'review',
  unique_a: 'unique · A',
  unique_b: 'unique · B',
}

export function ResultsStep({ sessionId, onReset }: Props) {
  const [data, setData] = useState<ResultsResponse | null>(null)
  const [filter, setFilter] = useState('pairs')
  const [page, setPage] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    getResults(sessionId, { matchClass: filter, page, pageSize: PAGE_SIZE })
      .then(setData)
      .catch((e) => setError((e as Error).message))
  }, [sessionId, filter, page])

  const c = data?.counts ?? {}
  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  return (
    <div>
      <div className="results-head">
        <h2>Results</h2>
        <a className="btn btn-primary" href={downloadUrl(sessionId)}>
          ⬇ Download CSV
        </a>
      </div>

      {/* Reconciliation: every row of each file is accounted for. */}
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
            {countFor(c, f.key) !== undefined && <span className="tab-count">{countFor(c, f.key)}</span>}
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}
      {data && data.pairs.length === 0 && <p className="muted">No records in this view.</p>}

      <div className="pair-list">
        {data?.pairs.map((p, i) => (
          <PairCard key={`${p.idx_a}-${p.idx_b}-${i}`} pair={p} />
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

function countFor(c: Record<string, number>, key: string): number | undefined {
  if (key === 'pairs') return c.pairs
  return c[key]
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

function PairCard({ pair }: { pair: MatchPair }) {
  const hasA = Object.keys(pair.record_a).length > 0
  const hasB = Object.keys(pair.record_b).length > 0
  const twoSided = hasA && hasB
  const keys = Array.from(new Set([...Object.keys(pair.record_a), ...Object.keys(pair.record_b)]))
  const cls = pair.match_class

  return (
    <div className={`pair-card ${cls}`}>
      <div className="pair-meta">
        <span className={`badge ${cls}`}>{CLASS_LABEL[cls] ?? cls}</span>
        {twoSided && <span className="score">{(pair.score * 100).toFixed(0)}%</span>}
        {pair.stage && <span className="stage-tag">{pair.stage}</span>}
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
