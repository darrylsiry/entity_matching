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
| 0 · Normalize | Strip legal suffixes (Inc/LLC/Ltd/GmbH), fold unicode, extract domains from URLs/emails, canonicalize IDs; **drop weak signals** (generic domains like gmail/linkedin, placeholder/short IDs) | `cleanco`, `tldextract`, `unidecode` | — |
| 1 · Deterministic | Exact match on registration/tax ID, then website domain | pandas | Near-certain |
| 2 · Probabilistic | Fellegi–Sunter linkage with **blocking** (empty values excluded) + field comparisons (Jaro-Winkler name, exact domain, Levenshtein address, exact postcode) | **Splink** (DuckDB) | Scored 0–1 |
| 3 · Fuzzy fallback | Length-aware name similarity `min(token_sort, token_set)` + address corroboration / conflict penalty | `rapidfuzz` | Scored 0–1 |
| 4 · Resolve | Split links by confidence (≥ Match auto, Review band → human). Two-file: **Match / Review / No-match**. One-file: confident links → **connected components → master ID**; review-band links → **possible duplicates** to confirm/dismiss | pandas | — |

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

When `ground_truth_matches.csv` is present and the uploaded data corresponds to it, the
Results screen shows a live precision/recall box (developer aid; ignored otherwise).

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

## Tests

```bash
cd backend && .venv/bin/pytest -q
```

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
