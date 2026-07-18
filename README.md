# Entity Matching

Find identical company entities two ways:

- **Compare two files** (linkage) — match records in file A against file B. Output:
  matched pairs, with unmatched records flagged so counts reconcile per file.
  Uncertain pairs land in a **review queue** where you **confirm** each as a match
  (folded into the final dataset) or **dismiss** it (records revert to unmatched).
- **Deduplicate one file** — cluster duplicate rows within a single file and give each
  row a **master ID** (rows sharing an ID are the same company). Only *confident*
  duplicates merge automatically; borderline pairs surface as **possible duplicates**
  you confirm (merge) or dismiss — the same review idea as link mode. Key fields are
  auto-detected (a collapsible "Detected fields" panel lets you confirm or correct
  them). Output: your file annotated with a `master_id` column reflecting your decisions.

Both paths run the same **cascade of open-source techniques in series**,
strongest-signal-first, so it scales to 100k+ records without comparing every possible
pair. Only what gets compared differs (A↔B vs rows within one file) and the output shape.

## How matching works — the cascade

Records are normalized into one canonical schema, then flow through stages. Anything an
early stage confidently resolves is removed from the pool the later, more expensive
stages consider.

| Stage | Technique | Library | Signal |
|-------|-----------|---------|--------|
| 0 · Normalize | Strip legal suffixes, **expand abbreviations** (Intl→International), fold unicode, extract domains, canonicalize IDs/**phones (E.164)**/**countries (ISO)**/states, derive **metaphone** keys, **parse addresses into components** (libpostal, optional — falls back to St→Street-style expansion); drop weak signals (generic domains, placeholder IDs/phones) | `cleanco`, `tldextract`, `unidecode`, `pycountry`, `phonenumbers`, `jellyfish`, `postal`* | — |
| 1 · Deterministic | Exact match on registration/tax ID (**type-guarded**), website domain, **phone** — domain/phone hits with conflicting names are **demoted to review**, not auto-accepted | pandas | Near-certain |
| 2 · Probabilistic | Fellegi–Sunter linkage with **blocking** (name tokens, postcode, domain, phone, **phonetic**; empties excluded) + field comparisons with **term-frequency weighting** (rare names count for more); with libpostal, address compares as **house number (exact) + street (edit distance)** instead of the whole string | **Splink** (DuckDB) | Scored 0–1 |
| 3 · Fuzzy fallback | Length-aware name similarity `min(token_sort, token_set)` + corroboration (address/city/zip/state/country) with conflict penalty — address corroboration uses **parsed house+street** when available; **records which adjustments applied** | `rapidfuzz` | Scored 0–1 |
| 3.5 · Semantic (opt-in) | **Embeddings** catch what no string metric can — "IBM" ↔ "International Business Machines". Runs only on **residuals** no earlier stage touched; candidates are **capped at the review band** (never auto-match) unless city/postcode corroborates | `sentence-transformers`*, `hnswlib`* | Review-capped |
| 4 · Resolve | Split links by confidence (≥ Match auto, Review band → human; optional **per-stage thresholds**). Two-file: **Match / Review / No-match**. One-file: confident links → **connected components → master ID**; review-band links → **possible duplicates** to confirm/dismiss | pandas | — |

**Blocking** is what makes 100k×100k tractable: instead of 10¹⁰ comparisons, records are
only compared when they share a signal (a name-token prefix, a postcode, a domain).

The exact ordering, tools, and every threshold/parameter are documented in plain
English in **[MATCHING_WORKFLOW.txt](MATCHING_WORKFLOW.txt)**. For how the app is wired
together (backend/frontend/Docker), see **[ARCHITECTURE.txt](ARCHITECTURE.txt)**.

### Accuracy

The matcher is tuned to keep false positives out of the auto-**Match** band and push
genuinely uncertain pairs to **Review**. Measured on the bundled 1,000×1,000 test set
against `ground_truth_matches.csv` (200 true pairs):

| | Precision | Recall | F1 | False positives |
|---|---|---|---|---|
| Auto-matches | **1.00** | 0.99 | **0.995** | **0** |

**Evaluation is a feature:** upload your own labeled sample of true matches
(columns `file_a_name,file_b_name`) on the Results screen to see live precision /
recall / F1 — and get a **suggested match threshold** (best F1 over your data),
applicable with one click. Thresholds can be changed and results **re-classified
instantly without re-running** the pipeline.

### Reviewing efficiently

The review queue supports **bulk actions** (confirm/dismiss all in view), **keyboard
review** (<kbd>↑↓</kbd>/<kbd>j k</kbd> navigate, <kbd>y</kbd> confirm, <kbd>n</kbd>
dismiss, <kbd>u</kbd> undo), and every pair shows **why it matched** — the stage/rule
plus corroboration chips (`+zip`, `−addr`, …). Results survive a page refresh and are
link-shareable (the session id lives in the URL).

## Project layout

```
backend/            FastAPI app + matching pipeline (Python)
  app/matching/     normalize, deterministic, probabilistic, fuzzy, scoring,
                    pipeline (two-file), cluster + dedupe (single-file)
  app/review.py     confirm/dismiss decisions overlaid on results (two-file)
  app/dedupe_review.py  possible-duplicate confirm/dismiss, re-clustering (single-file)
  app/evaluation.py precision/recall vs ground truth (developer aid)
  app/api/routes.py HTTP endpoints
  tests/            pytest unit tests
frontend/           Vite + React + TypeScript wizard UI
  public/           bundled sample CSVs offered as "test data" links on upload
sample_data/        generator: two files with overlaps + one file with duplicates
companies_file_a.csv / _b.csv, ground_truth_matches.csv   realistic 1k-row test set
MATCHING_WORKFLOW.txt / ARCHITECTURE.txt / DEPLOY.md      plain-English docs
```

## Prerequisites

- **Python 3.11** (Splink/pandas/rapidfuzz wheels; 3.12/3.13 also fine — 3.14 lacks some wheels today)
- **Node 18+**

## Run it

**One-time setup** — create the backend venv and install all deps:

```bash
make install
# equivalent to:
#   cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
#   cd frontend && npm install
```

**Optional: structured address parsing (libpostal).** Addresses match noticeably
better with [libpostal](https://github.com/openvenues/libpostal) installed — "123 Main
St Ste 4" parses into house number + street and compares component-wise. It needs the
C library plus a ~2 GB data download, so it is **strictly optional**: without it the
pipeline automatically falls back to built-in abbreviation expansion (St→Street,
Ste→Suite, …) and logs which mode is active. To enable it:

```bash
brew install libpostal                     # macOS (Linux: build from source, see the libpostal README)
cd backend && .venv/bin/pip install postal # the Python binding (uncomment it in requirements.txt to pin)
```

No config needed — `use_libpostal` defaults to on and auto-detects the library. CI and
the test suite exercise the fallback path.

**Optional: semantic matching (embeddings).** An opt-in Stage 3.5 embeds each
still-unmatched record with a small local model (`all-MiniLM-L6-v2`, CPU, no API
calls) and surfaces name pairs that mean the same company but share no spelling —
acronyms vs expansions, trade names vs legal names. Found pairs go to the **review
queue only** (capped below the match threshold) unless city/postcode agrees. Install
and enable:

```bash
cd backend && .venv/bin/pip install sentence-transformers   # + hnswlib for >20k residual rows
```

Then turn on `use_embedding` in the Configure step (config key; default off —
`embedding_threshold = 0.85`, `embedding_model = "all-MiniLM-L6-v2"`). If enabled
without the package installed, the stage logs a warning and is skipped.

**Start the app** — backend + frontend together, Ctrl+C stops both:

```bash
make dev          # or: ./dev.sh
```

Then open <http://localhost:5173> and use the wizard: **Choose mode → Upload → Map
columns → Configure → Run → Results** (dedupe mode skips *Map columns*). The Vite dev
server proxies `/api` to the backend on `:8000`, and the backend runs with `--reload`
so code edits restart it automatically.

Prefer separate terminals? `make backend` and `make frontend` run each side alone.

**Fastest way to try it:** on the Upload step, click the small **"test data"** link
under each *Choose file* button — it loads the bundled `companies_file_a.csv` /
`companies_file_b.csv` (1,000 rows each) straight into the uploader, so you can go
Upload → Run in a couple of clicks.

**Demo data** — small, hand-crafted files with deliberate name/format/ID variations:

```bash
make sample       # -> file_a.csv + file_b.csv (7 matches + 2 to review), file_dedupe.csv (12 rows → 7 entities)
```

For two-file mode, upload `file_a.csv` + `file_b.csv` and accept the auto-guessed
mapping. For dedupe mode, upload `file_dedupe.csv`.

## Deploy

The app ships as a single Docker image (frontend built in, served same-origin with the
API). See **[DEPLOY.md](DEPLOY.md)** for Fly.io steps. Quick local check:

```bash
docker build -t entity-matching .
docker run -p 8080:8080 entity-matching   # -> http://localhost:8080
```

## Tests & tooling

```bash
cd backend && .venv/bin/pytest -q          # unit tests
cd backend && .venv/bin/ruff check app     # lint (config in backend/pyproject.toml)
cd frontend && npm run format:check        # prettier
cd frontend && npm run e2e                 # Playwright e2e (see playwright.config.ts)
cd frontend && npm run gen:api             # regen TS types from the OpenAPI schema
```

- **CI** (`.github/workflows/ci.yml`) runs lint + tests, the frontend build with a
  prettier check, an **API-type drift check** (regenerates `backend/openapi.json` +
  `frontend/src/api-schema.d.ts` and fails if they differ from what's committed), and
  the Playwright e2e against the prod-mode server.
- **Pre-commit** hooks (ruff, prettier): `pip install pre-commit && pre-commit install`.
- Frontend API types are **generated from the backend's OpenAPI schema** — change a
  Pydantic model, run `python backend/scripts/export_openapi.py backend/openapi.json`
  then `npm run gen:api`, and TypeScript flags any mismatch.
- Uploads are capped (100 MB / 500k rows) and CSV exports are sanitized against
  spreadsheet formula injection.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/fields` | Canonical fields to map columns onto |
| POST | `/api/upload` | Upload a CSV/XLSX; returns `file_id` + detected columns |
| POST | `/api/session` | Create a session: `mode` (`link`\|`dedupe`) + file(s) + mappings + config |
| POST | `/api/session/{id}/run` | Start matching (async background job) |
| GET | `/api/session/{id}/status` | Poll `{state, stage, progress, counts}` |
| GET | `/api/session/{id}/results` | *link mode:* paginated pairs (`?match_class=pairs\|match\|review\|dismissed\|unique_a\|unique_b`) |
| POST | `/api/session/{id}/decision` | confirm/dismiss/undo — a review pair (link) or a possible-duplicate candidate (dedupe) |
| POST | `/api/session/{id}/decisions/bulk` | apply one decision to many pairs at once |
| POST | `/api/session/{id}/reclassify` | change thresholds and re-classify stored scores (no re-run) |
| POST | `/api/session/{id}/ground_truth` | upload a labeled sample to score against (P/R/F1 + threshold suggestion) |
| GET | `/api/session/{id}/clusters` | *dedupe mode:* master-ID clusters, reflecting confirmed merges (`?filter=&page=`) |
| GET | `/api/session/{id}/candidates` | *dedupe mode:* possible-duplicate pairs to review (`?filter=pending\|confirmed\|dismissed`) |
| GET | `/api/session/{id}/evaluation` | *debug:* precision/recall vs ground truth, when present |
| GET | `/api/session/{id}/download` | Results as CSV (matched pairs incl. confirmed reviews, or original file + `master_id`) |

## Notes / next steps

- Storage is local parquet under `backend/data/` (override with `ENTITY_MATCHING_DATA_DIR`).
  Swap for object storage / Postgres for multi-user or persistence.
- Job runs are in-process (`BackgroundTasks`); move to Celery/RQ + Redis to scale out.
- The probabilistic stage learns its parameters unsupervised (u via random sampling,
  m via EM). On tiny inputs EM may not fully converge — it degrades gracefully to the
  deterministic + fuzzy stages. On real 100k+ data it carries most of the load.
- No auth in the prototype.
