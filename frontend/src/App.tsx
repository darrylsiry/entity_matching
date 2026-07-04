import { useEffect, useState } from 'react'
import {
  getFields,
  type FieldSpec,
  type SessionConfig,
  type UploadResponse,
} from './api'
import { UploadStep } from './components/UploadStep'
import { ColumnMapStep } from './components/ColumnMapStep'
import { ConfigStep } from './components/ConfigStep'
import { RunStep } from './components/RunStep'
import { ResultsStep } from './components/ResultsStep'
import './App.css'

export type Mapping = Record<string, string>

const STEPS = ['Upload', 'Map columns', 'Configure', 'Run', 'Results'] as const

const DEFAULT_CONFIG: SessionConfig = {
  use_deterministic: true,
  use_probabilistic: true,
  use_fuzzy: true,
  thresholds: { match: 0.9, review: 0.75 },
  fuzzy_threshold: 0.8,
  probabilistic_threshold: 0.75,
}

export default function App() {
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

  function reset() {
    setUploadA(null)
    setUploadB(null)
    setMappingA({})
    setMappingB({})
    setConfig(DEFAULT_CONFIG)
    setSessionId(null)
    setStep(0)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Entity Matching</h1>
        <p>Match identical company records across two files.</p>
      </header>

      <ol className="stepper">
        {STEPS.map((label, i) => (
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
        {step === 0 && (
          <UploadStep
            uploadA={uploadA}
            uploadB={uploadB}
            onA={setUploadA}
            onB={setUploadB}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && uploadA && uploadB && (
          <ColumnMapStep
            fields={fields}
            uploadA={uploadA}
            uploadB={uploadB}
            mappingA={mappingA}
            mappingB={mappingB}
            setMappingA={setMappingA}
            setMappingB={setMappingB}
            onBack={() => setStep(0)}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <ConfigStep
            config={config}
            setConfig={setConfig}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && uploadA && uploadB && (
          <RunStep
            uploadA={uploadA}
            uploadB={uploadB}
            mappingA={mappingA}
            mappingB={mappingB}
            config={config}
            sessionId={sessionId}
            setSessionId={setSessionId}
            onBack={() => setStep(2)}
            onDone={() => setStep(4)}
          />
        )}
        {step === 4 && sessionId && (
          <ResultsStep sessionId={sessionId} onReset={reset} />
        )}
      </main>
    </div>
  )
}
