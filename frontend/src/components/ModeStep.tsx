// Wizard step 1 — choose between the two matching modes: "compare two files"
// (link) or "deduplicate one file" (dedupe). Picking a card advances to Upload.
import type { Mode } from '../api'

interface Props {
  mode: Mode
  onChoose: (m: Mode) => void
}

const OPTIONS: { mode: Mode; title: string; icon: string; desc: string; out: string }[] = [
  {
    mode: 'link',
    title: 'Compare two files',
    icon: '⇄',
    desc: 'Upload two lists and find records that refer to the same company across them.',
    out: 'Output: matched pairs (A ↔ B), with unmatched records flagged.',
  },
  {
    mode: 'dedupe',
    title: 'Deduplicate one file',
    icon: '⊚',
    desc: 'Upload a single list that may contain duplicate rows for the same company.',
    out: 'Output: every row gets a master ID; duplicate rows share the same ID.',
  },
]

export function ModeStep({ mode, onChoose }: Props) {
  return (
    <div>
      <h2>What do you want to do?</h2>
      <p className="muted">Choose how you want to match entities.</p>
      <div className="mode-grid">
        {OPTIONS.map((o) => (
          <button
            key={o.mode}
            className={`mode-card ${mode === o.mode ? 'selected' : ''}`}
            onClick={() => onChoose(o.mode)}
          >
            <div className="mode-icon">{o.icon}</div>
            <div className="mode-title">{o.title}</div>
            <div className="muted mode-desc">{o.desc}</div>
            <div className="mode-out">{o.out}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
