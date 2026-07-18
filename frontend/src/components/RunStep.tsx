// Wizard "Run" step — creates the session, starts the background matching job,
// polls /status every 500ms into a progress bar, and on completion shows the
// summary counts + per-stage timings. "View results" hands the session id up.
import { useEffect, useRef, useState } from 'react'
import {
  createSession,
  getStatus,
  runSession,
  type Mode,
  type RegistrationIdType,
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
  idTypeA: RegistrationIdType
  idTypeB: RegistrationIdType
  config: SessionConfig
  sessionId: string | null
  setSessionId: (id: string) => void
  onBack: () => void
  onDone: (sessionId: string) => void
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
        registration_id_type_a: props.idTypeA,
        registration_id_type_b: props.idTypeB,
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

  // Recursive timeout (not an interval) so a slow /status response never
  // overlaps the next poll.
  function poll(id: string) {
    pollRef.current = window.setTimeout(async () => {
      try {
        const st = await getStatus(id)
        setStatus(st)
        if (st.state === 'done' || st.state === 'error') {
          if (st.state === 'error') setError(st.error ?? 'matching failed')
        } else {
          poll(id)
        }
      } catch (e) {
        setError((e as Error).message)
      }
    }, 500)
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current)
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
            <span aria-hidden="true">▶</span> {dedupe ? 'Start deduplication' : 'Start matching'}
          </button>
        </div>
      ) : (
        <div className="progress-box">
          <div
            className="progress-bar"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={dedupe ? 'Deduplication progress' : 'Matching progress'}
          >
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="progress-label" aria-live="polite">
            <span>{status?.stage ?? 'Starting…'}</span>
            <span>{pct}%</span>
          </div>
          {done && status && (
            <div className="counts">
              {dedupe ? (
                <>
                  <Count label="Rows" value={status.counts.records} />
                  <Count label="Entities" value={status.counts.clusters} tone="match" />
                  <Count
                    label="Duplicate rows"
                    value={status.counts.duplicate_rows}
                    tone="review"
                  />
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
          {done && status?.counts.stage_timings && (
            <div className="timings muted small">
              {Object.entries(status.counts.stage_timings).map(([stage, secs]) => (
                <span key={stage} className="timing">
                  {stage.replace(/ \(.*\)/, '')}: <strong>{secs}s</strong>
                </span>
              ))}
              {status.counts.elapsed_seconds != null && (
                <span className="timing">
                  total: <strong>{status.counts.elapsed_seconds}s</strong>
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <div className="actions">
        <button
          className="btn btn-ghost"
          onClick={props.onBack}
          disabled={started && !done && !error}
        >
          <span aria-hidden="true">←</span> Back
        </button>
        <button
          className="btn btn-primary"
          disabled={!done || !props.sessionId}
          onClick={() => props.sessionId && props.onDone(props.sessionId)}
        >
          View results <span aria-hidden="true">→</span>
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
