import { useEffect, useState } from 'react'
import {
  getFields,
  type FieldSpec,
  type Mode,
  type SessionConfig,
  type UploadResponse,
} from './api'
import { autoMap, type Mapping } from './mapping'
import { ModeStep } from './components/ModeStep'
import { UploadStep } from './components/UploadStep'
import { ColumnMapStep } from './components/ColumnMapStep'
import { ConfigStep } from './components/ConfigStep'
import { RunStep } from './components/RunStep'
import { ResultsStep } from './components/ResultsStep'
import { DedupeResultsStep } from './components/DedupeResultsStep'
import './App.css'

export type { Mapping } from './mapping'

const STEPS: Record<Mode, string[]> = {
  link: ['Mode', 'Upload', 'Map columns', 'Configure', 'Run', 'Results'],
  dedupe: ['Mode', 'Upload', 'Configure', 'Run', 'Results'],
}

const DEFAULT_CONFIG: SessionConfig = {
  use_deterministic: true,
  use_probabilistic: true,
  use_fuzzy: true,
  thresholds: { match: 0.9, review: 0.75 },
  fuzzy_threshold: 0.8,
  probabilistic_threshold: 0.75,
}

export default function App() {
  const [mode, setMode] = useState<Mode>('link')
  const [step, setStep] = useState(0)
  const [fields, setFields] = useState<FieldSpec[]>([])

  const [uploadA, setUploadA] = useState<UploadResponse | null>(null)
  const [uploadB, setUploadB] = useState<UploadResponse | null>(null)
  const [mappingA, setMappingA] = useState<Mapping>({})
  const [mappingB, setMappingB] = useState<Mapping>({})
  const [config, setConfig] = useState<SessionConfig>(DEFAULT_CONFIG)
  const [sessionId, setSessionId] = useState<string | null>(null)

  useEffect(() => {
    getFields().then(setFields).catch(() => setFields([]))
  }, [])

  const steps = STEPS[mode]
  const current = steps[step]

  function reset() {
    setUploadA(null)
    setUploadB(null)
    setMappingA({})
    setMappingB({})
    setConfig(DEFAULT_CONFIG)
    setSessionId(null)
    setStep(0)
  }

  const go = (label: string) => setStep(Math.max(0, steps.indexOf(label)))

  function chooseMode(m: Mode) {
    setMode(m)
    setStep(1) // -> Upload
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
            config={config}
            sessionId={sessionId}
            setSessionId={setSessionId}
            onBack={() => go('Configure')}
            onDone={() => go('Results')}
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
