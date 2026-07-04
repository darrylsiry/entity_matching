import { useEffect, useState } from 'react'
import { downloadUrl, getClusters, type Cluster, type ClustersResponse } from '../api'

interface Props {
  sessionId: string
  onReset: () => void
}

const PAGE_SIZE = 25
const FILTERS = [
  { key: 'duplicates', label: 'Duplicate groups' },
  { key: 'all', label: 'All' },
  { key: 'unique', label: 'Unique' },
]

export function DedupeResultsStep({ sessionId, onReset }: Props) {
  const [data, setData] = useState<ClustersResponse | null>(null)
  const [filter, setFilter] = useState('duplicates')
  const [page, setPage] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    getClusters(sessionId, { filter, page, pageSize: PAGE_SIZE })
      .then(setData)
      .catch((e) => setError((e as Error).message))
  }, [sessionId, filter, page])

  const c = data?.counts ?? {}
  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  return (
    <div>
      <div className="results-head">
        <h2>Deduplication results</h2>
        <a className="btn btn-primary" href={downloadUrl(sessionId)}>
          ⬇ Download CSV
        </a>
      </div>

      <p className="muted small">
        Every row was assigned a <strong>master ID</strong>; rows sharing an ID are the
        same company. The CSV is your original file with <code>master_id</code> and{' '}
        <code>cluster_size</code> columns added.
      </p>

      <div className="summary-row">
        <Chip label="Rows" value={c.records} />
        <Chip label="Unique entities" value={c.clusters} tone="match" />
        <Chip label="Duplicate rows" value={c.duplicate_rows} tone="review" />
        <Chip label="Duplicate groups" value={c.duplicate_clusters} />
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
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}
      {data && data.clusters.length === 0 && <p className="muted">No groups in this view.</p>}

      <div className="pair-list">
        {data?.clusters.map((cl) => <ClusterCard key={cl.master_id} cluster={cl} />)}
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

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const dup = cluster.size > 1
  // Fields (rows) x members (columns), like the two-file pair view.
  const keys = Array.from(
    new Set(cluster.members.flatMap((m) => Object.keys(m.record))),
  )
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
