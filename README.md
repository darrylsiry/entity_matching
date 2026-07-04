# Entity Matching

Upload two files of company records and find identical entities across them. Matching
runs through a **cascade of open-source techniques in series**, strongest signal first,
so it scales to 100k+ records per file without comparing every possible pair.

## How matching works — the cascade

Both files are normalized into one canonical schema, then records flow through stages.
Anything an early stage confidently resolves is removed from the pool the later,
more expensive stages have to consider.

| Stage | Technique | Library | Signal |
|-------|-----------|---------|--------|
| 0 · Normalize | Strip legal suffixes (Inc/LLC/Ltd/GmbH), fold unicode, extract domains from URLs/emails, canonicalize IDs | `cleanco`, `tldextract`, `unidecode` | — |
| 1 · Deterministic | Exact match on registration/tax ID, then website domain | pandas | Near-certain |
| 2 · Probabilistic | Fellegi–Sunter linkage with **blocking** + fuzzy field comparisons (Jaro-Winkler name, exact domain, Levenshtein address, exact postcode) | **Splink** (DuckDB) | Scored 0–1 |
| 3 · Fuzzy fallback | Blocked token-set name similarity for leftovers | `rapidfuzz` | Scored 0–1 |
| 4 · Classify | Best match per record → **Match / Review / No-match** by threshold | pandas | — |

**Blocking** is what makes 100k×100k tractable: instead of 10¹⁰ comparisons, records are
only compared when they share a signal (a name-token prefix, a postcode, a domain).

## Project layout

```
backend/            FastAPI app + matching pipeline (Python)
  app/matching/     normalize, deterministic, probabilistic, fuzzy, scoring, pipeline
  app/api/routes.py HTTP endpoints
  tests/            pytest unit tests
frontend/           Vite + React + TypeScript wizard UI
sample_data/        generator for two demo files with known overlaps
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

**Demo data** — two files with deliberate name/format/ID variations (7 known matches):

```bash
make sample       # -> sample_data/file_a.csv, file_b.csv
```

Upload those two files, accept the auto-guessed column mapping, and run.

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
| POST | `/api/session` | Pair two files + column mappings + config |
| POST | `/api/session/{id}/run` | Start matching (async background job) |
| GET | `/api/session/{id}/status` | Poll `{state, stage, progress, counts}` |
| GET | `/api/session/{id}/results` | Paginated match pairs (`?match_class=&page=`) |
| GET | `/api/session/{id}/download` | Full results as CSV |

## Notes / next steps

- Storage is local parquet under `backend/data/` (override with `ENTITY_MATCHING_DATA_DIR`).
  Swap for object storage / Postgres for multi-user or persistence.
- Job runs are in-process (`BackgroundTasks`); move to Celery/RQ + Redis to scale out.
- The probabilistic stage learns its parameters unsupervised (u via random sampling,
  m via EM). On tiny inputs EM may not fully converge — it degrades gracefully to the
  deterministic + fuzzy stages. On real 100k+ data it carries most of the load.
- No auth in the prototype.
