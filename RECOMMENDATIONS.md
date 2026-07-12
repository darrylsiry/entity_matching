# Entity Matching — Holistic Review & Recommendations

A candid review of the app as it stands, against what I believe it's *for*: helping
someone with messy company data trustably find matches/duplicates, at real data sizes,
in a way that fits their workflow — with the human-review step made efficient and their
data kept safe. Recommendations are grouped by area and tagged **[impact / effort]**
(H/M/L). A prioritized roadmap is at the end.

Anchored to the current code, not generic advice.

---

## Where the app is already strong

- The **matching engine is cleanly decoupled** (`app/matching/*` are pure functions
  over DataFrames) — easy to test and reason about. Keep this boundary sacred.
- The **cascade + reconciliation invariant** (`matched + review + unique = total`) is a
  genuinely good design: results always add up, which builds user trust.
- **Human-in-the-loop review** (link + dedupe) with a decision *overlay* (not rewriting
  results) is the right pattern.
- **Accuracy work paid off**: 0.47 → 1.00 precision on the golden set. The recent
  fixes (empty-block exclusion, length-aware metric, corroboration) were the right calls.
- **One-image deploy** (FastAPI serves the built SPA) is simple and correct for the scale.

## The three things that matter most (if you do nothing else)

1. **Durability & the job model** — jobs live in memory (`jobs.py`) and files on local
   disk (`storage.py`). A restart loses in-flight runs; you can only ever run one
   machine. This caps reliability and scale. *(Architecture #1)*
2. **The review bottleneck** — at real scale, confirming pairs one-by-one is the actual
   product bottleneck. Bulk actions + the score-provenance "why" + LLM adjudication are
   the highest-leverage UX investments. *(UX #1, #2)*
3. **Make it scriptable (API/CLI)** — today it's UI-only. Most serious entity-resolution
   work is a repeatable batch step in a pipeline. A stable API/CLI multiplies the app's
   usefulness. *(Architecture #3)*

---

## 1. Architecture

**1.1 Durable job queue — replace in-process `BackgroundTasks` + in-memory registry.** `[H/M]`
Current: `jobs.py` keeps state in a process-local dict; `routes.run_session` uses
FastAPI `BackgroundTasks`. Consequences: runs die on deploy/restart, no cancellation,
no ret/scale-out, and `fly.toml` is pinned to `min_machines_running=1` because of it.
→ Move to a durable queue (RQ/Arq/Celery + Redis). Persist job state (status, stage,
progress, counts) in the store so `/status` survives restarts and any worker can serve
it. Enables cancellation, retries, and horizontal scale.

**1.2 Real persistence — Postgres (metadata) + object storage (files).** `[H/M]`
Current: parquet/JSON on local disk keyed by id; fine for one user, but no concurrency
control, no durability, no multi-tenant. → Postgres for sessions/decisions/job state;
S3-compatible object storage (or Fly Volumes → Tigris) for uploads/results. Keep
`storage.py` as the single seam — it's already the right abstraction point.

**1.3 API-first + a CLI.** `[H/M]`
The engine is UI-only. Expose the existing endpoints as a documented, versioned public
API (they already are clean REST) and add a thin CLI (`entity-match a.csv b.csv
--config …`) so it can run headless in a data pipeline. Publish an OpenAPI spec (FastAPI
gives it free) and generate the client. This is probably the single biggest expansion of
who can use the tool.

**1.4 Package the matching engine separately.** `[M/L]`
`app/matching/` has no web/storage deps. Extract it as an installable library
(`entity_matching_core`) so it can be unit-tested, versioned, reused in notebooks, and
depended on by both the API and CLI. Low effort given the existing boundary.

**1.5 Kill the hand-maintained type contract.** `[M/L]`
`models.py` (Pydantic) and `frontend/src/api.ts` (TS types) are duplicated by hand — a
standing drift/bug risk. → Generate TS types from the OpenAPI schema
(`openapi-typescript`) in CI. One source of truth.

**1.6 Out-of-core for very large files.** `[M/M]`
`routes.upload` does `pd.read_csv(...).astype("string")` — the whole file into RAM;
`service.enrich_pairs`/`dedupe_download_frame` reload the *entire* raw parquet on every
results page and build the full enriched frame for download. On a 2 GB machine with two
100k files + Splink + DuckDB this is tight. → Stream uploads to disk; use DuckDB/Polars
to read only the rows a page needs; generate the download CSV in a streaming pass.
(You already run DuckDB via Splink — lean on it.)

**1.7 Persist results *with* their config + engine version.** `[M/L]`
Config is stored per session (good), but add the engine/version and a content hash so
runs are reproducible and comparable, and so you can offer "re-run with new settings and
diff." Foundation for the calibration idea (spec 4.2).

---

## 2. Tooling & Operations

**2.1 CI/CD — there is none checked in.** `[H/L]`
No `.github/workflows`. → Add CI: `pytest` (incl. the fallback-path + golden regression
tests), frontend `tsc`/build, and the Playwright e2e you've been running by hand
(commit it!). Gate merges on green. Build/push the Docker image on tag.

**2.2 Lint / format / type-check.** `[M/L]`
No ruff/black/mypy or eslint/prettier config present. → Add `ruff` + `black` + `mypy`
(backend), `eslint` + `prettier` (frontend), wired into pre-commit and CI. Cheap,
compounding quality.

**2.3 Observability.** `[M/M]`
Logging is minimal (`logging.basicConfig`), no metrics/timing. Users can't tell why a
run is slow and you can't debug prod. → Structured logs with a run id; record per-stage
timing + candidate-pair counts in the job record and surface them in the UI ("Splink:
1.2s, 340k pairs"). Add Sentry (or similar) for errors; optional OpenTelemetry.

**2.4 Dependency hygiene & reproducibility.** `[M/L]`
Backend deps are pinned but there's no lockfile/hashes; Splink EM isn't seeded. → Adopt
`uv`/`pip-tools` with a lockfile; pin seeds where libraries allow so two runs on the
same input are identical (the spec's determinism requirement); add Dependabot + a vuln
scan (`pip-audit`, `npm audit`) in CI.

**2.5 Container hardening.** `[M/L]`
Run as non-root, pin the base image by digest, add the `HEALTHCHECK` (Fly checks it but
the image should too), and keep the heavy optional deps (libpostal, torch) in a separate
image target so the default stays lean.

**2.6 Test depth.** `[M/M]`
Backend unit tests are good; add: property-based tests for normalizers (`hypothesis`),
a **committed** golden-file regression fixture, a load test at 100k to catch memory/
time cliffs, and frontend component tests (Vitest + RTL). The Playwright flows you drive
manually should be committed CI e2e.

---

## 3. User Experience

**3.1 Make bulk review a first-class feature.** `[H/M]`
Reviewing pairs individually doesn't scale past a few dozen. → Add: "confirm/dismiss all
in this filter", select-all-above-score, keyboard shortcuts (Y/N/U to confirm/dismiss/
undo, arrows to move), and group-by-rule ("accept all deterministic id matches"). This
is the difference between usable and unusable at 10k pairs.

**3.2 "Why did this match?" — surface provenance.** `[H/M]`
Reviewers need to see *why* a pair scored what it did (stage, rule, which fields agreed/
conflicted). This is spec item 1.8 — prioritize it; it directly increases review speed
and trust. Show a compact evidence chip per pair ("name 0.86 · same postcode · address
conflict").

**3.3 Don't lose work on refresh.** `[H/L]`
`session_id`/results live only in React state (`App.tsx`) — a refresh or shared link
loses everything. → Persist the session id in the URL (`/s/:sessionId`) and/or
localStorage; make results shareable/bookmarkable. Cheap, big quality-of-life win.

**3.4 Column mapping improvements.** `[M/M]`
Show a data preview per column while mapping; auto-detect and warn on unmapped required
fields; a "use the same mapping for both files" toggle; remember mappings for repeat
files. Reduces the most error-prone step.

**3.5 Richer results view.** `[M/M]`
Sort/filter results by score/stage/field-agreement; search by company name; a
histogram of scores so users can *see* where to set thresholds. Let them adjust
thresholds and re-classify **without re-running** the whole pipeline (scores are
already stored).

**3.6 Better feedback during runs.** `[M/M]`
Progress is a single bar. → Stream counts as stages complete ("deterministic: 182
matched"), show elapsed/estimated time, and add a **Cancel** button (needs the durable
queue, 1.1).

**3.7 Flexible export.** `[M/L]`
Let users choose columns, export matches-only vs everything, and (link mode) write the
match id/decision back onto the *original* file layout. Offer Excel as well as CSV.

**3.8 Accessibility & theming.** `[M/M]`
Dark-only, hand-rolled CSS. Audit color contrast (the gold-on-dark diff highlight is
borderline), add keyboard focus states and ARIA on the interactive review controls, and
a light theme. Consider adopting a small accessible component base to avoid re-inventing.

**3.9 Data-quality preflight.** `[M/M]`
Before running, tell the user what you see: "80% of rows have no website; 12 duplicate
names within file A; country column looks unmapped." Sets expectations and improves
inputs. The evaluation harness (below) should be promoted from "debug" to a real
"score against my labeled sample" feature.

---

## 4. Performance & Effectiveness (match quality)

**4.1 Continue the improvements spec — but sequence by leverage.** `[H/varies]`
`MATCHING_IMPROVEMENTS_SPEC.md` is a strong plan. Highest bang-for-buck first: the
**corroboration guard (1.6)** and **provenance (1.8)** for trust; **phonetic blocking
(1.4)** and **TF adjustments (1.5)** for recall/precision; then **LLM adjudication
(Phase 3)** which directly attacks the review bottleneck. Embeddings (2.2) and libpostal
(2.1) are higher-cost; gate on need.

**4.2 Make evaluation a product feature, not a debug box.** `[H/M]`
`evaluation.py` already computes precision/recall vs a labeled file. Promote it: let the
user upload their *own* ground-truth sample, see P/R/F1, and get **threshold
recommendations** (spec 4.2 — isotonic calibration from decisions). This turns tuning
from guesswork into measurement and is a real differentiator.

**4.3 Calibrate scores across stages.** `[M/M]`
Scores from deterministic (flat 0.99), Splink (probability), and fuzzy (adjusted ratio)
aren't on one scale but share one threshold. Per-stage thresholds (spec 1.8) is the
stopgap; true calibration (map each stage's raw score → empirical match probability from
review decisions) makes the single "confidence %" honest.

**4.4 Determinism.** `[M/L]`
Splink EM and any future ANN aren't seeded, so re-runs can differ — bad for a data tool
users must trust. Pin seeds; assert two runs match in a test.

**4.5 Blocking efficiency.** `[M/M]`
The fuzzy stage builds Python dicts and iterates candidate sets (`fuzzy.py`). Fine now;
at 100k residuals it can get slow. Consider expressing blocking as DuckDB joins (as
Splink does) for the fuzzy/embedding stages too, so it's one scalable mechanism.

**4.6 Incremental / master-data matching.** `[M/H]`
A large latent use case: match a *new* file against an already-resolved "golden" set and
keep stable entity IDs over time (MDM). Today master IDs are per-run and there's no
persisted canonical store. Worth exploring — it's a natural, high-value evolution of the
dedupe mode.

---

## 5. Security & Data Governance

**5.1 CSV formula injection on export.** `[H/L]`
The download endpoints write user cell values straight to CSV. A value like
`=cmd|…` or `+HYPERLINK(...)` executes when opened in Excel/Sheets. → Prefix cells
beginning with `= + - @` with `'` on export. Small change, real vulnerability for a data
tool.

**5.2 Upload limits & validation.** `[M/L]`
`routes.upload` accepts arbitrary-size files with no cap and parses Excel via openpyxl
(whole file in memory). → Enforce a max size/row count, validate content type, and
consider that malformed/malicious spreadsheets are an attack surface.

**5.3 Authentication.** `[H/M]`
No auth — anyone with the URL uploads and runs. Fine for a local/demo tool; required
before any shared/hosted deployment. Add auth (Clerk/Auth0/simple SSO) and per-user data
isolation (depends on 1.2).

**5.4 LLM / external egress consent (Phase 3, 4.4).** `[M/L]`
Any feature that sends uploaded company data off-box (LLM adjudication, GLEIF/
OpenCorporates enrichment) needs an explicit, logged consent step and a clear notice.
Build it in from the start.

**5.5 Rate limiting & abuse.** `[L/L]`
Add basic rate limiting on upload/run if ever public (Fly WAF or app-level).

---

## Suggested roadmap

**Quick wins (days, high value, low risk)**
- CSV formula-injection fix (5.1)
- CI with existing tests + committed Playwright e2e (2.1)
- ruff/black/mypy + eslint/prettier + pre-commit (2.2)
- Persist session in URL/localStorage (3.3)
- Upload size/row limits (5.2)
- Generate TS types from OpenAPI (1.5)

**Near-term (the current spec + review UX)**
- Finish improvements spec Phase 1, esp. provenance (1.8) and corroboration (1.6)
- Bulk review actions + keyboard shortcuts + "why did this match" chips (3.1, 3.2)
- Promote evaluation to a real "score my labeled sample + suggest thresholds" feature (4.2)
- Adjust thresholds & re-classify without re-running (3.5)
- Structured logging + per-stage timing surfaced in UI (2.3)

**Strategic (weeks, unlocks scale & new users)**
- Durable job queue + Postgres/object storage; drop the single-machine constraint (1.1, 1.2)
- API-first + CLI; package the engine (1.3, 1.4)
- Out-of-core large-file handling (1.6)
- LLM adjudication of the review band (spec Phase 3)
- Auth + multi-tenancy (5.3)
- Incremental / master-data matching against a persisted golden set (4.6)
