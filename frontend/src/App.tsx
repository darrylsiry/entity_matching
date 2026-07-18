import { useEffect, useState } from 'react'
import {
  getFields,
  getStatus,
  type FieldSpec,
  type Mode,
  type RegistrationIdType,
  type SessionConfig,
  type UploadResponse,
} from './api'
import { autoMap, type Mapping } from './mapping'
import { Landing } from './components/Landing'
import { ModeStep } from './components/ModeStep'
import { UploadStep } from './components/UploadStep'
import { ColumnMapStep } from './components/ColumnMapStep'
import { ConfigStep } from './components/ConfigStep'
import { RunStep } from './components/RunStep'
import { ResultsStep } from './components/ResultsStep'
import { DedupeResultsStep } from './components/DedupeResultsStep'
import './App.css'

export type { Mapping } from './mapping'

// ════════════════════════════════════════════════════════════════════════
// SECTION: Wizard configuration — the step list per mode and the default
// matching config (mirrors the backend SessionConfig defaults).
// ════════════════════════════════════════════════════════════════════════

const STEPS: Record<Mode, string[]> = {
  link: ['Mode', 'Upload', 'Map columns', 'Configure', 'Run', 'Results'],
  dedupe: ['Mode', 'Upload', 'Configure', 'Run', 'Results'],
}

export const DEFAULT_CONFIG: SessionConfig = {
  use_deterministic: true,
  use_probabilistic: true,
  use_fuzzy: true,
  thresholds: { match: 0.9, review: 0.75 },
  fuzzy_threshold: 0.8,
  probabilistic_threshold: 0.75,
  use_phone: true,
  default_phone_region: 'US',
  use_phonetic_blocking: true,
  splink_tf_adjustments: true,
  deterministic_corroboration: true,
  deterministic_name_floor: 0.3,
  deterministic_demoted_score: 0.8,
  stage_thresholds: null,
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Session persistence — results survive refresh and are shareable
// The completed session lives in the URL hash (#s=<id>&m=<mode>) and
// localStorage; on load we offer a "Resume results" banner if the server
// still has the results.
// ════════════════════════════════════════════════════════════════════════

const STORAGE_KEY = 'entity-matching:last-session'

interface SavedSession {
  sessionId: string
  mode: Mode
}

function readSavedSession(): SavedSession | null {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const fromHash = hash.get('s')
  if (fromHash) {
    return { sessionId: fromHash, mode: hash.get('m') === 'dedupe' ? 'dedupe' : 'link' }
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SavedSession) : null
  } catch {
    return null
  }
}

function saveSession(saved: SavedSession | null) {
  try {
    if (saved) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved))
      window.history.replaceState(null, '', `#s=${saved.sessionId}&m=${saved.mode}`)
    } else {
      localStorage.removeItem(STORAGE_KEY)
      window.history.replaceState(null, '', window.location.pathname)
    }
  } catch {
    /* private mode etc. — persistence is best-effort */
  }
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: The wizard shell
// Owns all cross-step state (mode, uploads, mappings, id types, config,
// session id) and renders whichever step component is current. Step
// components stay stateless about the overall flow.
// ════════════════════════════════════════════════════════════════════════

export default function App() {
  // The marketing landing shows first on a fresh visit; a shared results link
  // (#s=<id>) skips straight to the app so the resume flow isn't buried.
  const [showLanding, setShowLanding] = useState(() => !window.location.hash.includes('s='))
  const [mode, setMode] = useState<Mode>('link')
  const [step, setStep] = useState(0)
  const [fields, setFields] = useState<FieldSpec[]>([])

  const [uploadA, setUploadA] = useState<UploadResponse | null>(null)
  const [uploadB, setUploadB] = useState<UploadResponse | null>(null)
  const [mappingA, setMappingA] = useState<Mapping>({})
  const [mappingB, setMappingB] = useState<Mapping>({})
  const [idTypeA, setIdTypeA] = useState<RegistrationIdType>('unknown')
  const [idTypeB, setIdTypeB] = useState<RegistrationIdType>('unknown')
  const [config, setConfig] = useState<SessionConfig>(DEFAULT_CONFIG)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [resumable, setResumable] = useState<SavedSession | null>(null)

  useEffect(() => {
    getFields()
      .then(setFields)
      .catch(() => setFields([]))
    // Offer to resume a previous session if its results still exist on the server.
    const saved = readSavedSession()
    if (saved) {
      getStatus(saved.sessionId)
        .then((st) => {
          if (st.state === 'done') setResumable(saved)
        })
        .catch(() => saveSession(null))
    }
  }, [])

  const steps = STEPS[mode]
  const current = steps[step]

  function reset() {
    setUploadA(null)
    setUploadB(null)
    setMappingA({})
    setMappingB({})
    setIdTypeA('unknown')
    setIdTypeB('unknown')
    setConfig(DEFAULT_CONFIG)
    setSessionId(null)
    saveSession(null)
    setStep(0)
  }

  const go = (label: string) => setStep(Math.max(0, steps.indexOf(label)))

  function chooseMode(m: Mode) {
    setMode(m)
    setStep(1) // -> Upload
  }

  function resume() {
    if (!resumable) return
    setMode(resumable.mode)
    setSessionId(resumable.sessionId)
    setResumable(null)
    setStep(STEPS[resumable.mode].indexOf('Results'))
  }

  function onRunComplete(id: string) {
    saveSession({ sessionId: id, mode })
    go('Results')
  }

  // Leaving Upload: dedupe auto-maps its single file and skips column mapping.
  function afterUpload() {
    if (mode === 'dedupe' && uploadA) {
      setMappingA(autoMap(fields, uploadA))
      go('Configure')
    } else {
      go('Map columns')
    }
  }

  if (showLanding) {
    return <Landing onStart={() => setShowLanding(false)} />
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Entity Matching</h1>
        <p>Find identical company records — across two files or within one.</p>
      </header>

      <ol className="stepper">
        {steps.map((label, i) => (
          <li
            key={label}
            className={`step ${i === step ? 'active' : ''} ${i < step ? 'done' : ''}`}
          >
            <span className="dot">{i < step ? '✓' : i + 1}</span>
            {label}
          </li>
        ))}
      </ol>

      <main className="panel">
        {current === 'Mode' && resumable && (
          <div className="resume-banner">
            <span>
              You have previous {resumable.mode === 'dedupe' ? 'deduplication' : 'matching'}{' '}
              results.
            </span>
            <button className="btn btn-sm btn-primary" onClick={resume}>
              Resume results →
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => {
                saveSession(null)
                setResumable(null)
              }}
            >
              Dismiss
            </button>
          </div>
        )}
        {current === 'Mode' && <ModeStep mode={mode} onChoose={chooseMode} />}

        {current === 'Upload' && (
          <UploadStep
            mode={mode}
            uploadA={uploadA}
            uploadB={uploadB}
            onA={setUploadA}
            onB={setUploadB}
            onBack={() => go('Mode')}
            onNext={afterUpload}
          />
        )}

        {current === 'Map columns' && uploadA && uploadB && (
          <ColumnMapStep
            fields={fields}
            uploadA={uploadA}
            uploadB={uploadB}
            mappingA={mappingA}
            mappingB={mappingB}
            setMappingA={setMappingA}
            setMappingB={setMappingB}
            idTypeA={idTypeA}
            idTypeB={idTypeB}
            setIdTypeA={setIdTypeA}
            setIdTypeB={setIdTypeB}
            onBack={() => go('Upload')}
            onNext={() => go('Configure')}
          />
        )}

        {current === 'Configure' && (
          <ConfigStep
            mode={mode}
            config={config}
            setConfig={setConfig}
            fields={fields}
            upload={uploadA}
            mapping={mappingA}
            setMapping={setMappingA}
            onBack={() => go(mode === 'dedupe' ? 'Upload' : 'Map columns')}
            onNext={() => go('Run')}
          />
        )}

        {current === 'Run' && uploadA && (
          <RunStep
            mode={mode}
            uploadA={uploadA}
            uploadB={uploadB}
            mappingA={mappingA}
            mappingB={mappingB}
            idTypeA={idTypeA}
            idTypeB={idTypeB}
            config={config}
            sessionId={sessionId}
            setSessionId={setSessionId}
            onBack={() => go('Configure')}
            onDone={onRunComplete}
          />
        )}

        {current === 'Results' && sessionId && mode === 'link' && (
          <ResultsStep sessionId={sessionId} onReset={reset} />
        )}
        {current === 'Results' && sessionId && mode === 'dedupe' && (
          <DedupeResultsStep sessionId={sessionId} onReset={reset} />
        )}
      </main>
    </div>
  )
}
