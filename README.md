# Entity Matching

Find identical company entities two ways:

- **Compare two files** (linkage) — match records in file A against file B. Output:
  matched pairs, with unmatched records flagged so counts reconcile per file.
- **Deduplicate one file** — cluster duplicate rows within a single file and give each
  row a **master ID** (rows sharing an ID are the same company). Key fields are
  auto-detected (a collapsible "Detected fields" panel on the Configure step lets you
  confirm or correct them). Output: your file annotated with a `master_id` column.

Both paths run the same **cascade of open-source techniques in series**,
strongest-signal-first, so it scales to 100k+ records without comparing every possible
pair. Only what gets compared differs (A↔B vs rows within one file) and the output shape.

## How matching works — the cascade

Records are normalized into one canonical schema, then flow through stages. Anything an
early stage confidently resolves is removed from the pool the later, more expensive
stages consider.

| Stage | Technique | Library | Signal |
|-------|-----------|---------|--------|
| 0 · Normalize | Strip legal suffixes (Inc/LLC/Ltd/GmbH), fold unicode, extract domains from URLs/emails, canonicalize IDs | `cleanco`, `tldextract`, `unidecode` | — |
| 1 · Deterministic | Exact match on registration/tax ID, then website domain | pandas | Near-certain |
| 2 · Probabilistic | Fellegi–Sunter linkage with **blocking** + fuzzy field comparisons (Jaro-Winkler name, exact domain, Levenshtein address, exact postcode) | **Splink** (DuckDB) | Scored 0–1 |
| 3 · Fuzzy fallback | Blocked token-set name similarity for leftovers | `rapidfuzz` | Scored 0–1 |
| 4 · Resolve | Two-file: best match per record → **Match / Review / No-match**. One-file: union all links → **connected components → master ID** per row | pandas | — |

**Blocking** is what makes 100k×100k tractable: instead of 10¹⁰ comparisons, records are
only compared when they share a signal (a name-token prefix, a postcode, a domain).

## Project layout

```
backend/            FastAPI app + matching pipeline (Python)
  app/matching/     normalize, deterministic, probabilistic, fuzzy, scoring,
                    pipeline (two-file), cluster + dedupe (single-file)
  app/api/routes.py HTTP endpoints
  tests/            pytest unit tests
frontend/           Vite + React + TypeScript wizard UI
sample_data/        generator: two files with overlaps + one file with duplicates
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

Then open <http://localhost:5173> and use the 5-step wizard: **Upload → Map columns →
Configure → Run → Results** (with CSV export). The Vite dev server proxies `/api` to the
backend on `:8000`, and the backend runs with `--reload` so code edits restart it
automatically.

Prefer separate terminals? `make backend` and `make frontend` run each side alone.

**Demo data** — deliberate name/format/ID variations:

```bash
make sample       # -> file_a.csv + file_b.csv (7 matches), file_dedupe.csv (12 rows → 7 entities)
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
| POST | `/api/upload` | Upload a CSV/XLSX; returns `file_id` + detected columns |
| POST | `/api/session` | Create a session: `mode` (`link`\|`dedupe`) + file(s) + mappings + config |
| POST | `/api/session/{id}/run` | Start matching (async background job) |
| GET | `/api/session/{id}/status` | Poll `{state, stage, progress, counts}` |
| GET | `/api/session/{id}/results` | *link mode:* paginated match pairs (`?match_class=&page=`) |
| GET | `/api/session/{id}/clusters` | *dedupe mode:* master-ID clusters (`?filter=&page=`) |
| GET | `/api/session/{id}/download` | Results as CSV (matched pairs, or original file + `master_id`) |

## Notes / next steps

- Storage is local parquet under `backend/data/` (override with `ENTITY_MATCHING_DATA_DIR`).
  Swap for object storage / Postgres for multi-user or persistence.
- Job runs are in-process (`BackgroundTasks`); move to Celery/RQ + Redis to scale out.
- The probabilistic stage learns its parameters unsupervised (u via random sampling,
  m via EM). On tiny inputs EM may not fully converge — it degrades gracefully to the
  deterministic + fuzzy stages. On real 100k+ data it carries most of the load.
- No auth in the prototype.
