import { useEffect, useRef, useState } from 'react'
import {
  createSession,
  getStatus,
  runSession,
  type Mode,
  type SessionConfig,
  type StatusResponse,
  type UploadResponse,
} from '../api'
import type { Mapping } from '../mapping'

interface Props {
  mode: Mode
  uploadA: UploadResponse
  uploadB: UploadResponse | null
  mappingA: Mapping
  mappingB: Mapping
  config: SessionConfig
  sessionId: string | null
  setSessionId: (id: string) => void
  onBack: () => void
  onDone: () => void
}

export function RunStep(props: Props) {
  const dedupe = props.mode === 'dedupe'
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [started, setStarted] = useState(false)
  const pollRef = useRef<number | null>(null)

  async function start() {
    setError(null)
    setStarted(true)
    try {
      const { session_id } = await createSession({
        mode: props.mode,
        file_id_a: props.uploadA.file_id,
        mapping_a: props.mappingA,
        file_id_b: dedupe ? null : props.uploadB?.file_id,
        mapping_b: dedupe ? {} : props.mappingB,
        config: props.config,
      })
      props.setSessionId(session_id)
      await runSession(session_id)
      poll(session_id)
    } catch (e) {
      setError((e as Error).message)
      setStarted(false)
    }
  }

  function poll(id: string) {
    pollRef.current = window.setInterval(async () => {
      try {
        const st = await getStatus(id)
        setStatus(st)
        if (st.state === 'done' || st.state === 'error') {
          if (pollRef.current) window.clearInterval(pollRef.current)
          if (st.state === 'error') setError(st.error ?? 'matching failed')
        }
      } catch (e) {
        if (pollRef.current) window.clearInterval(pollRef.current)
        setError((e as Error).message)
      }
    }, 500)
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  }, [])

  const pct = Math.round((status?.progress ?? 0) * 100)
  const done = status?.state === 'done'

  return (
    <div>
      <h2>{dedupe ? 'Run deduplication' : 'Run matching'}</h2>
      <p className="muted">
        {dedupe
          ? `Scanning ${props.uploadA.rows.toLocaleString()} rows for duplicates through the selected stages.`
          : `Matching ${props.uploadA.rows.toLocaleString()} × ${(props.uploadB?.rows ?? 0).toLocaleString()} records through the selected stages.`}
      </p>

      {!started ? (
        <div className="run-cta">
          <button className="btn btn-primary btn-lg" onClick={start}>
            ▶ {dedupe ? 'Start deduplication' : 'Start matching'}
          </button>
        </div>
      ) : (
        <div className="progress-box">
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="progress-label">
            <span>{status?.stage ?? 'Starting…'}</span>
            <span>{pct}%</span>
          </div>
          {done && status && (
            <div className="counts">
              {dedupe ? (
                <>
                  <Count label="Rows" value={status.counts.records} />
                  <Count label="Entities" value={status.counts.clusters} tone="match" />
                  <Count label="Duplicate rows" value={status.counts.duplicate_rows} tone="review" />
                  <Count label="Dup. groups" value={status.counts.duplicate_clusters} />
                </>
              ) : (
                <>
                  <Count label="Matches" value={status.counts.match} tone="match" />
                  <Count label="Review" value={status.counts.review} tone="review" />
                  <Count label="Records A" value={status.counts.records_a} />
                  <Count label="Records B" value={status.counts.records_b} />
                </>
              )}
            </div>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <div className="actions">
        <button className="btn btn-ghost" onClick={props.onBack} disabled={started && !done && !error}>
          ← Back
        </button>
        <button className="btn btn-primary" disabled={!done} onClick={props.onDone}>
          View results →
        </button>
      </div>
    </div>
  )
}

function Count({ label, value, tone }: { label: string; value?: number; tone?: string }) {
  return (
    <div className={`count ${tone ?? ''}`}>
      <div className="count-value">{(value ?? 0).toLocaleString()}</div>
      <div className="count-label">{label}</div>
    </div>
  )
}
