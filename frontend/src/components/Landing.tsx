import { useEffect, useRef, useState } from 'react'
import './Landing.css'

// ════════════════════════════════════════════════════════════════════════
// SECTION: Live match-feed demo data — real pairs the engine would produce,
// one per stage family, cycled in the hero so the product's core moment
// (two differently-spelled records recognized as one company) sells itself.
// ════════════════════════════════════════════════════════════════════════

interface DemoPair {
  rowA: string
  nameA: string
  fieldsA: string
  rowB: string
  nameB: string
  fieldsB: string
  score: string
  cls: 'match' | 'review'
  provenance: string[]
}

const DEMO_PAIRS: DemoPair[] = [
  {
    rowA: '112',
    nameA: 'Acme, Inc.',
    fieldsA: 'EIN 12-3456789 · acme.com',
    rowB: '87',
    nameB: 'ACME INCORPORATED',
    fieldsB: 'Tax ID 123456789 · www.acme.com/about',
    score: '0.99',
    cls: 'match',
    provenance: ['stage: deterministic', 'rule: registration_id'],
  },
  {
    rowA: '204',
    nameA: 'Jonson Controls',
    fieldsA: 'Milwaukee · 53202',
    rowB: '451',
    nameB: 'Johnson Controls',
    fieldsB: 'Milwaukee · 53202',
    score: '0.96',
    cls: 'match',
    provenance: ['stage: fuzzy', 'phonetic block: JNSN', '+ same_city', '+ same_postcode'],
  },
  {
    rowA: '377',
    nameA: 'IBM',
    fieldsA: 'armonk · us',
    rowB: '29',
    nameB: 'International Business Machines',
    fieldsB: '(no location on file)',
    score: '0.89',
    cls: 'review',
    provenance: ['stage: embedding', 'semantic cosine 0.94', 'capped → human review'],
  },
  {
    rowA: '58',
    nameA: 'Global Print Ltd',
    fieldsA: 'globalprint.co.uk',
    rowB: '440',
    nameB: 'GP Holdings',
    fieldsB: 'globalprint.co.uk',
    score: '0.80',
    cls: 'review',
    provenance: [
      'stage: deterministic',
      'rule: domain_name_conflict',
      'shared site, names differ → review',
    ],
  },
]

function MatchFeed() {
  const [index, setIndex] = useState(0)
  const [fading, setFading] = useState(false)
  const reduced = useRef(window.matchMedia('(prefers-reduced-motion: reduce)').matches)

  useEffect(() => {
    if (reduced.current) return
    const cycle = setInterval(() => {
      setFading(true)
      setTimeout(() => {
        setIndex((i) => (i + 1) % DEMO_PAIRS.length)
        setFading(false)
      }, 400)
    }, 4200)
    return () => clearInterval(cycle)
  }, [])

  const p = DEMO_PAIRS[index]
  return (
    <div className="ld-demo" aria-label="Example of two records being matched">
      <div className="ld-demo-head">
        <span>live match feed</span>
        <span className="ld-demo-dots">
          {DEMO_PAIRS.map((_, i) => (
            <span key={i} className={i === index ? 'on' : ''} />
          ))}
        </span>
      </div>
      <div className={`ld-demo-body ${fading ? 'out' : ''}`}>
        <div className="ld-rec">
          <span className="ld-rec-src">File A · row {p.rowA}</span>
          <span className="ld-rec-name">{p.nameA}</span>
          <span className="ld-rec-fields">{p.fieldsA}</span>
        </div>
        <div className="ld-connector">
          <span className="ld-wire" />
          <span className="ld-verdict">
            <span className="ld-score">{p.score}</span>
            <span className={`ld-chip ld-chip-${p.cls}`}>{p.cls}</span>
          </span>
          <span className="ld-wire" />
        </div>
        <div className="ld-rec">
          <span className="ld-rec-src">File B · row {p.rowB}</span>
          <span className="ld-rec-name">{p.nameB}</span>
          <span className="ld-rec-fields">{p.fieldsB}</span>
        </div>
        <div className="ld-demo-prov">
          {p.provenance.map((t) => (
            <span key={t} className="ld-chip ld-chip-adj">
              {t}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// SECTION: Static page content — the cascade, stats, workflow, and modes.
// ════════════════════════════════════════════════════════════════════════

const STAGES = [
  {
    no: '0',
    name: 'Normalize',
    tools: ['cleanco', 'libpostal', 'phonenumbers'],
    body: (
      <>
        Every record enters one canonical form: legal suffixes stripped, abbreviations expanded,
        phones to E.164, countries to ISO codes, addresses parsed into house number + street.{' '}
        <strong>“Café Ltd.” and “CAFE LIMITED” become the same string</strong> before matching even
        starts.
      </>
    ),
    pool: 100,
    cap: '100% of records — the full pool',
  },
  {
    no: '1',
    name: 'Deterministic',
    tools: ['pandas'],
    body: (
      <>
        Exact joins on registration ID, website domain, and phone — with guards. Shared domain but
        conflicting names? That's a subsidiary pattern, and it goes to{' '}
        <strong>review, not auto-match.</strong>
      </>
    ),
    pool: 58,
    cap: 'residuals after the easy wins (illustrative)',
  },
  {
    no: '2',
    name: 'Probabilistic',
    tools: ['Splink', 'DuckDB'],
    body: (
      <>
        Fellegi–Sunter linkage, self-trained on your data — no labels needed. Blocking keeps
        100k×100k tractable; term-frequency weighting knows that{' '}
        <strong>matching on “Zyxwv Corp” means more than matching on “Global Services.”</strong>
      </>
    ),
    pool: 30,
    cap: 'residuals',
  },
  {
    no: '3',
    name: 'Fuzzy',
    tools: ['rapidfuzz', 'jellyfish'],
    body: (
      <>
        Typo-tolerant name similarity with phonetic blocking — “Jonson” still finds “Johnson.”
        Agreeing city and postcode raise confidence; a{' '}
        <strong>clearly different address pushes a coincidence down</strong> into review.
      </>
    ),
    pool: 12,
    cap: 'residuals',
  },
  {
    no: '3.5',
    name: 'Semantic',
    tools: ['sentence-transformers'],
    body: (
      <>
        Optional embeddings catch what no string metric can —{' '}
        <strong>“IBM” ↔ “International Business Machines.”</strong> Runs locally on CPU, and its
        finds are capped below the match bar: suggestions for you, never silent merges.
      </>
    ),
    pool: 5,
    cap: 'the truly hard residuals',
  },
]

const WORKFLOW = [
  { title: 'Choose mode', body: 'Link two files, or deduplicate one.' },
  {
    title: 'Upload',
    body: 'CSV or Excel, up to 500k rows. Bundled test data if you just want to try it.',
  },
  {
    title: 'Map columns',
    body: 'Your headers → canonical fields. Auto-guessed; you confirm.',
  },
  {
    title: 'Configure',
    body: 'Sensible defaults. Thresholds and stages are yours to tune — or not.',
  },
  { title: 'Run', body: 'The cascade executes with live stage-by-stage progress.' },
  {
    title: 'Review & export',
    body: 'Confirm or dismiss the borderline pairs, download the reconciled CSV.',
  },
]

const OSS_TOOLS = [
  'Splink',
  'DuckDB',
  'rapidfuzz',
  'cleanco',
  'libpostal',
  'phonenumbers',
  'pycountry',
  'jellyfish',
  'sentence-transformers',
  'FastAPI',
  'React',
]

export function Landing({ onStart }: { onStart: () => void }) {
  return (
    <div className="landing">
      <header className="ld-top">
        <div>
          <span className="ld-wordmark">
            Entity<span className="ld-tick">≡</span>Matching
          </span>
          <span className="ld-top-tag"> open-source record linkage, run locally</span>
        </div>
        <button className="ld-btn ld-btn-solid ld-btn-sm" onClick={onStart}>
          Get started →
        </button>
      </header>

      <div className="ld-hero">
        <div>
          <div className="ld-eyebrow">Company record linkage &amp; dedupe</div>
          <h1>
            The same company, spelled five different ways.
            <span className="ld-variants">
              “Acme, Inc.” · “ACME INCORPORATED” · “acme inc” · “Acme Intl Grp” · “The Acme
              International Group”
            </span>
          </h1>
          <p className="ld-lede">
            Entity Matching turns two messy company lists — or one file full of duplicates — into a
            single reconciled dataset. A cascade of open-source matchers does the work;{' '}
            <strong>anything it can't defend goes to you, not silently into your data.</strong>
          </p>
          <div className="ld-hero-ctas">
            <button className="ld-btn ld-btn-solid" onClick={onStart}>
              Get started
            </button>
            <a className="ld-btn ld-btn-ghost" href="#ld-cascade">
              See how it matches
            </a>
          </div>
        </div>
        <MatchFeed />
      </div>

      <section id="ld-cascade">
        <div className="ld-sec-head">
          <div className="ld-eyebrow">The approach</div>
          <h2>A waterfall of matchers, strongest evidence first.</h2>
          <p className="ld-sub">
            Every stage removes what it confidently resolves; the next stage only works the
            residuals. Strict, cheap methods take the easy wins — the fuzzy, expensive ones focus on
            the hard leftovers. That's how it stays fast at 100k+ rows, and precise everywhere.
          </p>
        </div>

        <div className="ld-cascade">
          {STAGES.map((s, i) => (
            <div className="ld-stage" data-step={i + 1} key={s.no}>
              <div className="ld-stage-no">{s.no}</div>
              <div className="ld-stage-body">
                <h3>
                  {s.name}{' '}
                  <span className="ld-tools">
                    {s.tools.map((t) => (
                      <span key={t} className="ld-chip ld-chip-tool">
                        {t}
                      </span>
                    ))}
                  </span>
                </h3>
                <p>{s.body}</p>
                <div className="ld-pool">
                  <div className="ld-pool-bar" style={{ width: `${s.pool}%` }} />
                  <div className="ld-pool-cap">{s.cap}</div>
                </div>
              </div>
            </div>
          ))}
          <div className="ld-basin">
            <h3>⇣ The review queue</h3>
            <p>
              Whatever the cascade can't decide with confidence lands here — scored, explained, and
              waiting for a human yes or no. Uncertainty is surfaced, never buried.
            </p>
          </div>
        </div>
      </section>

      <section>
        <div className="ld-sec-head">
          <div className="ld-eyebrow">Precision first</div>
          <h2>Tuned so “Match” means match.</h2>
        </div>
        <div className="ld-stats">
          <div className="ld-stat good">
            <div className="ld-stat-n">1.00</div>
            <div className="ld-stat-l">Precision (auto-match)</div>
          </div>
          <div className="ld-stat good">
            <div className="ld-stat-n">0</div>
            <div className="ld-stat-l">False positives</div>
          </div>
          <div className="ld-stat">
            <div className="ld-stat-n">0.99</div>
            <div className="ld-stat-l">Recall</div>
          </div>
          <div className="ld-stat">
            <div className="ld-stat-n">0.995</div>
            <div className="ld-stat-l">F1 score</div>
          </div>
        </div>
        <p className="ld-fine">
          Measured on the bundled 1,000 × 1,000 benchmark against 200 ground-truth pairs. And
          evaluation isn't a one-off: upload your own labeled sample and the app scores itself live
          — then suggests the threshold that maximizes F1 on <em>your</em> data, applied with one
          click.
        </p>
      </section>

      <section>
        <div className="ld-sec-head">
          <div className="ld-eyebrow">The workflow</div>
          <h2>Upload to reconciled export in six steps.</h2>
          <p className="ld-sub">
            A wizard walks you through; nothing to script, nothing to configure up front.
          </p>
        </div>
        <div className="ld-flow">
          {WORKFLOW.map((w, i) => (
            <div className="ld-step" key={w.title}>
              <span className="ld-step-k">STEP {i + 1}</span>
              <h3>{w.title}</h3>
              <p>{w.body}</p>
            </div>
          ))}
        </div>
        <div className="ld-notes">
          <div>
            <h3>Review at keyboard speed</h3>
            <p>
              <span className="ld-kbd">j</span>/<span className="ld-kbd">k</span> to move,{' '}
              <span className="ld-kbd">y</span> confirm, <span className="ld-kbd">n</span> dismiss,{' '}
              <span className="ld-kbd">u</span> undo — or bulk-decide a whole filtered view at once.
            </p>
          </div>
          <div>
            <h3>Every match explains itself</h3>
            <p>
              Each pair shows the stage that found it, the rule that fired, and the evidence that
              moved its score. No black-box verdicts.
            </p>
          </div>
          <div>
            <h3>Change your mind instantly</h3>
            <p>
              Every scored pair is kept, so moving a threshold re-classifies results in milliseconds
              — no re-run, no waiting.
            </p>
          </div>
        </div>
      </section>

      <section>
        <div className="ld-sec-head">
          <div className="ld-eyebrow">Open source, local first</div>
          <h2>Your data never leaves your machine.</h2>
          <p className="ld-sub">
            No SaaS upload, no per-record pricing, no API keys. The whole pipeline — including the
            embedding model — runs on your hardware, built on best-in-class open-source matching
            libraries:
          </p>
        </div>
        <div className="ld-oss">
          {OSS_TOOLS.map((t) => (
            <span key={t} className="ld-chip ld-chip-tool">
              {t}
            </span>
          ))}
        </div>
        <div className="ld-final">
          <h2>Ready when your data is.</h2>
          <p>
            Two files or one, 50 rows or 500,000 — the bundled test set means you can see results
            before touching your own data.
          </p>
          <button className="ld-btn ld-btn-solid ld-btn-lg" onClick={onStart}>
            Get started →
          </button>
        </div>
      </section>

      <footer className="ld-end">
        <span>Entity≡Matching — stepwise waterfall record linkage</span>
        <span>self-hosted · Docker image included</span>
      </footer>
    </div>
  )
}
