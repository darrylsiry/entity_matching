# Entity Matching Pipeline — Improvement Spec

**Audience:** Claude Code, implementing against the codebase described in `MATCHING_WORKFLOW.txt` (modules in `backend/app/matching/`: `pipeline`, `normalize`, `deterministic`, `probabilistic`, `fuzzy`, `scoring`, `cluster`, `dedupe`, `review`).

**Prime directives:**

1. Preserve the cascade architecture and the reconciliation invariant: per file, `matched + review + unique = total rows`.
2. Every new dependency must degrade gracefully (log a warning, skip the feature) if not installed — same pattern as the existing Splink fallback.
3. Every new behavior gets a `SessionConfig` flag with a safe default. Existing runs must produce identical results unless a new flag is turned on, **except** items marked `[default-on]`, which are correctness fixes.
4. Match pairs must carry provenance: which stage produced them and (new) which corroboration/adjustments were applied.

Work is organized into 4 phases. Each item is independently shippable; within a phase, order is by dependency.

---

## Phase 1 — Quick wins (light deps, high value)

### 1.1 Country / state normalization `[default-on]`

**Problem:** Splink compares city exactly and the fuzzy stage awards +0.03 for "same city"; country and state are only punctuation-stripped, so "USA" ≠ "United States", "CA" ≠ "California".

**Change (`normalize.py`):**
- Add dependency `pycountry`.
- `normalize_country(value) -> str`: resolve to ISO 3166-1 alpha-2 (lowercase), via exact alpha-2/alpha-3 lookup, then official/common-name lookup, then `pycountry.countries.search_fuzzy` wrapped in try/except. Unresolvable → return the cleaned input unchanged (never drop signal).
- `normalize_state(value, country) -> str`: for US/CA (extend later), map full names ↔ USPS/postal abbreviations via a small static dict in `normalize.py` (50 states + DC + territories; 13 CA provinces). Other countries: pass through cleaned.
- Wire both into the Stage 0 field map.

**Tests:** "USA"/"U.S.A."/"United States" → "us"; "California"/"CA" → "ca"; unknown strings pass through.

### 1.2 Company-name abbreviation expansion `[default-on]`

**Problem:** cleanco strips legal suffixes but "Acme Intl" vs "Acme International" and "Smith & Co" vs "Smith and Co" still score low.

**Change (`normalize.py`):**
- Static expansion table applied token-wise after cleanco, before the name-tokens key is built. Initial table (extendable module-level dict):
  `intl→international, natl→national, mfg→manufacturing, svcs→services, svc→service, grp→group, hldgs→holdings, bros→brothers, assoc→associates, dept→department, dev→development, tech→technology, sys→systems, sols→solutions, &→and, +→and`
- Also strip a leading token "the".
- Applied to the normalized name AND the name-tokens blocking key (so blocking benefits too).

**Tests:** "The Acme Intl Grp" and "Acme International Group" produce identical normalized names and token keys.

### 1.3 Phone number as a canonical field

**Problem:** Phone is often the second-strongest identifier after registration id and is currently unused.

**Change:**
- Add canonical field `phone` to the column-mapping/auto-detect list.
- `normalize.py`: dependency `phonenumbers`. `normalize_phone(value, country_iso2) -> str`: parse with the record's country as region hint (default region configurable, default `"US"`); output E.164; invalid/unparseable → `""`. **Guards** (return `""`): all-same-digit, fewer than 7 significant digits, known placeholder patterns (`555-555-…`, `123-456-7890`, `000…`).
- `deterministic.py`: add exact-match key `phone` at priority **3** (after registration_id, domain). Subject to the corroboration guard in **1.6** (shared switchboards behave like shared domains).
- `probabilistic.py`: if phone present, add exact-match comparison and add phone to blocking rules (empty-excluded, same wrapper as existing keys).

**Config:** `use_phone = true` (only takes effect if a phone column is mapped), `default_phone_region = "US"`.

### 1.4 Phonetic blocking key (Double Metaphone)

**Problem:** First-4-chars blocking (Splink fallback and the fuzzy stage) misses typos in the first 4 characters ("Jonson" vs "Johnson").

**Change:**
- Dependency `jellyfish` (pure-python wheel, no C build).
- `normalize.py`: derive `name_metaphone` = jellyfish metaphone of each name token, space-joined, plus a `first_token_metaphone` key.
- `probabilistic.py`: add `first_token_metaphone` as an additional blocking rule (empty-excluded).
- `fuzzy.py`: each record contributes its per-token metaphone keys **in addition to** the existing 4-char prefix keys. Union of both key sets → candidate pairs. `_PREFIX_LEN` behavior unchanged.

**Config:** `use_phonetic_blocking = true`.
**Note:** blocking-only change — scores are unaffected; this only adds candidate pairs, so it can only find more matches, never change existing scores.

### 1.5 Splink term-frequency adjustments

**Problem:** Common tokens ("Global", "Services", "Group") inflate name agreement; a match on a rare name should count for more than a match on a common one.

**Change (`probabilistic.py`):**
- On the name comparison, enable Splink 4 term-frequency adjustments on the exact-match level (`.configure(term_frequency_adjustments=True)` on the comparison level / use the TF-enabled variants from `splink.comparison_library`).
- Enable TF adjustments on the `city` exact-match comparison as well (city frequencies are very skewed).
- Keep everything else about the model identical. Verify EM still converges on the small-data path; the existing defensive try/except must cover any TF-related failure.

**Config:** `splink_tf_adjustments = true`.

### 1.6 Corroboration guard for domain / phone deterministic matches `[default-on]`

**Problem:** Stage 1 emits 0.99 for any shared domain. Subsidiaries, franchises, and portfolio companies share domains (and switchboard phones), producing confident false matches.

**Change (`deterministic.py`):**
- Applies to **domain** and **phone** keys only — never to `registration_id`.
- For each candidate pair matched on domain/phone alone, compute the fuzzy name score (reuse the Stage 3 metric: `min(token_sort_ratio, token_set_ratio)` on normalized names):
  - name score ≥ 0.30 → emit at 0.99 as today (provenance: `deterministic:domain+name_ok`).
  - name score < 0.30 → **demote**: emit at 0.80 (review band) with provenance `deterministic:domain_name_conflict`. Do **not** remove these records from the pool for later stages.
- If the pair ALSO matches on registration_id, no guard — emit 0.99.

**Config:** `deterministic_corroboration = true`, `deterministic_name_floor = 0.30`, `deterministic_demoted_score = 0.80`.

### 1.7 Registration-ID type guard

**Problem:** IDs are normalized to bare alphanumerics with no type; a DUNS in file A can numerically collide with an EIN in file B.

**Change:**
- Column mapping (link mode): when a column is mapped to `registration_id`, allow an optional **id type** selection per file: `duns | ein | vat | company_number | lei | other | unknown` (default `unknown`).
- `deterministic.py`: the registration_id key only joins when the two types are equal, or at least one side is `unknown`.
- Store the type in session config; no change to normalization itself.

**Config:** `registration_id_type_a`, `registration_id_type_b` (dedupe mode: single value).

### 1.8 Score provenance + per-stage thresholds

**Problem:** Scores from different stages sit on different scales (fixed 0.99, Splink probability, fuzzy adjusted ratio) but are classified by one global threshold pair.

**Change:**
- **Provenance `[default-on]`:** every emitted pair carries `stage` (`deterministic | probabilistic | fuzzy | embedding | llm`), `rule` (e.g. `registration_id`, `domain_name_conflict`), and for the fuzzy stage the list of applied adjustments (`addr_agree`, `addr_conflict`, `same_city`, `same_postcode`). Surface these in the results API/CSV and the review UI (a reviewer should see *why* the score is what it is).
- **Per-stage threshold overrides (default off/null):** `thresholds.match` / `thresholds.review` remain the global defaults; add optional `stage_thresholds = {probabilistic: {match, review}, fuzzy: {match, review}}`. Null → inherit global. Stage 4 classification consults the pair's stage.

---

## Phase 2 — Heavier normalization + new candidate source

### 2.1 libpostal address normalization

**Problem:** Address comparison (Splink Levenshtein [2,5]; fuzzy token_set with ±0.05/−0.18) operates on raw strings, so "123 Main St Ste 4" vs "123 Main Street Suite 4" reads as disagreement — and the −0.18 conflict penalty makes address errors actively harmful.

**Change:**
- Dependency: `postal` (pip) — **requires the libpostal C library (~2 GB data download)**. This must be strictly optional: import inside try/except at module load; if unavailable, fall back to a static abbreviation-expansion table (st→street, ave→avenue, rd→road, blvd→boulevard, ste/suite→suite, fl→floor, apt→apartment, n/s/e/w→north/…, etc.) applied token-wise. Log once which mode is active.
- `normalize.py`: `parse_address(raw) -> {house_number, road, unit, city, state, postcode}` using `postal.parser.parse_address`. Populate derived fields `addr_house_number`, `addr_road` (also run `postal.expand.expand_address` on the road, keep first expansion). If the record has no separate city/state/postcode mapped, backfill them from the parse (never overwrite user-mapped values).
- `probabilistic.py`: when parsed fields exist, replace the whole-string Levenshtein comparison with two comparisons: `addr_house_number` exact + `addr_road` Levenshtein [2, 5]. Otherwise keep current behavior.
- `fuzzy.py`: compute the address similarity for the ±adjustment on `addr_road + addr_house_number` (expanded) when available, else raw address as today. Thresholds (0.70 / 0.45) and deltas unchanged.

**Config:** `use_libpostal = true` (auto-falls back if not installed).
**Docs:** add install instructions (brew/apt libpostal + `pip install postal`) to the README; CI should run the fallback path.

### 2.2 Embedding-based semantic stage (Stage 3.5, optional)

**Problem:** No string metric catches "IBM" ↔ "International Business Machines" or "VW" ↔ "Volkswagen".

**Change — new module `embedding.py`, mirroring `fuzzy.py`'s interface:**
- Deps: `sentence-transformers` (model `all-MiniLM-L6-v2`), ANN via `hnswlib` (or sklearn `NearestNeighbors` under ~20k residual rows). All optional-import-guarded.
- Runs **only on records still unresolved** after Stage 3. Text = `"{normalized name}, {city}, {country_iso2}"` (skip empty parts).
- Link mode: index B residuals, query each A residual, top-k = 5, cosine similarity.
- Emission rules (conservative — embeddings hallucinate similarity):
  - cosine ≥ `embedding_threshold` (default **0.85**) → candidate.
  - Candidates are **capped at the review band**: score = `min(0.89, cosine)` so they can never auto-match on their own.
  - Exception: if city or postcode also agrees exactly, allow score = cosine uncapped.
  - Apply the same corroboration/conflict adjustments as the fuzzy stage (address ±, city/postcode +0.03) before capping.
- Dedupe mode: index all residuals, same rules, within-file pairs.
- Provenance stage = `embedding`.

**Config:** `use_embedding = false` (off by default), `embedding_threshold = 0.85`, `embedding_model = "all-MiniLM-L6-v2"`.

---

## Phase 3 — LLM adjudication of the review band

**Problem:** The review band is pure human work. An LLM can resolve most borderline pairs cheaply, with humans spot-checking.

**Change — new module `adjudicate.py`:**
- Dep: `anthropic` SDK; API key from `ANTHROPIC_API_KEY` env var. Optional-guarded: no key / no package → feature hidden in UI.
- Trigger: explicit user action ("Auto-review with AI") after the pipeline runs — never automatic.
- Input: all pairs currently classified REVIEW that have no human decision. Cap at `llm_max_pairs` (default 500), prioritized by score descending.
- Batching: up to 20 pairs per request. For each pair send **raw (pre-normalization) values** of every mapped field for both records, plus score, stage, and provenance rule.
- Prompt contract: respond with strict JSON array `[{pair_id, decision: "match" | "no_match" | "unsure", confidence: 0-1, reason: "<one sentence>"}]`. Parse defensively; any unparseable item → `unsure`.
- Application: reuse the existing review-overlay mechanism with a `source` field:
  - `match` → confirm (source=`llm`)
  - `no_match` → dismiss (source=`llm`)
  - `unsure` → stays in review for a human
- UI: LLM decisions visually distinct from human ones; human decisions always override; `Undo` works on LLM decisions; show the `reason` string in the review row.
- Export/counts: LLM-confirmed pairs count as matches, consistent with the overlay semantics that already exist.

**Config:** `llm_adjudication_enabled = false`, `llm_model = "claude-haiku-4-5"` (cheap default; user-selectable), `llm_max_pairs = 500`, `llm_min_confidence_to_apply = 0.8` (below → treat as unsure).

---

## Phase 4 — Deferred / larger options (spec-level only; confirm before building)

### 4.1 One-to-one assignment option (link mode)

Currently each A keeps its best B, but many A's can claim one B. Add `assignment_mode = "best_per_a" (default) | "one_to_one"`. One-to-one: sort all classified pairs by score desc (tie-break: stage priority deterministic > probabilistic > fuzzy > embedding, then row index for determinism); greedily accept pairs whose A and B are both unclaimed. Displaced pairs that still clear the review threshold → REVIEW with rule `conflict_displaced`; below → dropped. Reconciliation invariant must still hold. *(This is a behavior change users should opt into — some datasets legitimately have many-to-one, e.g. locations → parent.)*

### 4.2 Threshold calibration from review decisions

Human + LLM review decisions are labeled data. Add a maintenance job: once ≥ 200 decisions exist for a stage, fit isotonic regression (sklearn) mapping raw stage scores → empirical match probability; persist the calibration curve per stage and (optionally) suggest revised stage thresholds to the user. Never silently change thresholds — surface as a recommendation.

### 4.3 dedupe.io active-learning engine (alternative to Splink)

Offer `engine = "splink" (default) | "dedupe"` for Stage 2. The `dedupe` library asks the user to label ~20–30 uncertain pairs (fits naturally into the existing review UI) and trains a model. Larger lift: needs an interactive labeling loop in the UI. Spec fully before building.

### 4.4 External registry enrichment (network, opt-in)

New optional module `enrich.py`: resolve names against GLEIF LEI API (free) and optionally OpenCorporates (keyed) before matching; a resolved LEI becomes a deterministic key. Requires caching, rate limiting, and a clear "sends your data to a third party" consent step. Off by default.

---

## Cross-cutting requirements

**New config summary (SessionConfig additions):**

| key | default | phase |
|---|---|---|
| `use_phone` | true | 1.3 |
| `default_phone_region` | "US" | 1.3 |
| `use_phonetic_blocking` | true | 1.4 |
| `splink_tf_adjustments` | true | 1.5 |
| `deterministic_corroboration` | true | 1.6 |
| `deterministic_name_floor` | 0.30 | 1.6 |
| `deterministic_demoted_score` | 0.80 | 1.6 |
| `registration_id_type_a/_b` | "unknown" | 1.7 |
| `stage_thresholds` | null (inherit) | 1.8 |
| `use_libpostal` | true (auto-fallback) | 2.1 |
| `use_embedding` | false | 2.2 |
| `embedding_threshold` | 0.85 | 2.2 |
| `embedding_model` | "all-MiniLM-L6-v2" | 2.2 |
| `llm_adjudication_enabled` | false | 3 |
| `llm_model` | "claude-haiku-4-5" | 3 |
| `llm_max_pairs` | 500 | 3 |
| `llm_min_confidence_to_apply` | 0.8 | 3 |

**New dependencies:** `pycountry`, `phonenumbers`, `jellyfish` (Phase 1); `postal` + libpostal C lib (optional), `sentence-transformers`, `hnswlib` (Phase 2); `anthropic` (Phase 3). Everything beyond Phase 1 must import lazily and fail soft.

**Testing (every phase):**
1. Unit tests for each new normalizer (see per-item test notes).
2. Regression: a golden-file test — existing sample datasets run with all new flags at defaults must satisfy `matched + review + unique = total` and must not lose any pair that previously classified as MATCH (corroboration demotions to REVIEW are the one allowed diff; assert them explicitly).
3. Fallback-path tests: simulate missing optional deps (`postal`, `sentence-transformers`, `anthropic`) and assert the pipeline completes with a logged warning.
4. Determinism: two runs on the same input + config produce identical output (matters for EM seeds and ANN — pin seeds where the libraries allow).

**Update `MATCHING_WORKFLOW.txt`** (or regenerate it) after each phase so the plain-English reference stays true to the code.
