# Matching Improvements — Implementation Plan

Derived from `MATCHING_IMPROVEMENTS_SPEC.md`. Maps each spec item to concrete code
changes, ordered by dependency. Invariants held throughout: cascade architecture;
per-file `matched + review + unique = total`; every optional dep imports lazily and
fails soft; new behaviors are config-gated with safe defaults (except `[default-on]`
correctness fixes); every pair carries provenance.

Legend: `[default-on]` changes results by design (regression test asserts the diff).

---

## Phase 1 — Quick wins (light deps: pycountry, phonenumbers, jellyfish)

Foundational order:

- **P1-config** — Add all Phase-1 keys to `models.SessionConfig` + `ThresholdConfig`
  (and mirror onto `pipeline.MatchConfig`, thread via `service._to_match_config`).
  Frontend `ConfigStep` gets an "Advanced" section for the new toggles.
- **1.1 Country/state normalization `[default-on]`** — `normalize.py`:
  `normalize_country` (pycountry → ISO alpha-2, cached per unique value; unresolved →
  cleaned passthrough), `normalize_state(value, country)` (static US/CA dicts). Wire
  into `_FIELD_NORMALIZERS`. *Extension:* add a country-conflict term to
  `fuzzy.corroborate` and a country exact-match to Splink so the normalization has an
  effect. Tests: USA/U.S.A./United States → "us"; California/CA → "ca"; passthrough.
- **1.2 Abbreviation expansion `[default-on]`** — `normalize.py`: module-level
  `_ABBREVIATIONS` dict + leading-"the" strip, applied token-wise inside
  `normalize_name` (before the tokens key is built, so blocking benefits). Test:
  "The Acme Intl Grp" == "Acme International Group".
- **1.3 Phone field** — new `CanonicalField.PHONE`; `FieldSpec`; `normalize_phone`
  (phonenumbers → E.164, region hint, placeholder/short guards); `NORM_PHONE` derived
  col; deterministic key at priority 3 (guarded by 1.6); Splink exact-match + blocking
  (empty-excluded). Config `use_phone`, `default_phone_region`.
- **1.4 Phonetic blocking** — `normalize.py`: `name_metaphone` + `first_token_metaphone`
  (jellyfish). `probabilistic.py`: add first-token-metaphone blocking rule (empty-
  excluded). `fuzzy.py`: add per-token metaphone keys to `_block_keys` union. Blocking-
  only; scores unchanged. Config `use_phonetic_blocking`.
- **1.5 Splink TF adjustments** — `probabilistic.py`: enable term-frequency adjustments
  on name + city exact-match levels, inside the existing defensive try/except. Config
  `splink_tf_adjustments`.
- **1.6 Corroboration guard `[default-on]`** — `deterministic.py`: for domain/phone-only
  matches, compute `fuzzy.name_similarity`; ≥ floor (0.30) → 0.99
  (`rule=domain+name_ok`); < floor → demote to 0.80 (`rule=domain_name_conflict`), do
  NOT remove from pool. reg_id matches unaffected. Config
  `deterministic_corroboration`, `deterministic_name_floor`, `deterministic_demoted_score`.
- **1.7 Registration-ID type guard** — `CreateSessionRequest`:
  `registration_id_type_a/_b`. `deterministic.py`: reg_id join only when types equal or
  one is `unknown`. Frontend: id-type dropdown next to a reg_id mapping.
- **1.8 Provenance `[default-on]` + per-stage thresholds** — add `adjustments: list[str]`
  to the pair record + `MatchPair`/`DedupeCandidate` models; fuzzy records its applied
  adjustments; surface `stage`/`rule`/`adjustments` in results + CSV + review cards.
  Add optional `stage_thresholds` (null→inherit); `scoring.combine_and_classify`
  consults per-stage overrides. Config `stage_thresholds`.

**Phase-1 exit:** full pytest green; golden-file regression (sample datasets, all new
flags at default) holds the reconciliation invariant and loses no prior MATCH except
explicit 1.6 demotions (asserted). Update `MATCHING_WORKFLOW.txt`. **Then pause.**

---

## Phase 2 — Heavier normalization + semantic candidates (optional deps)

- **2.1 libpostal** — `normalize.py`: `parse_address` via `postal` inside try/except at
  import; **static abbreviation-expansion fallback** when absent (log which mode).
  Derived `addr_house_number`/`addr_road`; backfill city/state/postcode only when
  unmapped. `probabilistic.py`: swap whole-string Levenshtein for house_number-exact +
  road-Levenshtein when parsed. `fuzzy.py`: address similarity on expanded road+number.
  Config `use_libpostal` (auto-fallback). **Not in default requirements/Docker** —
  documented optional install. Only the fallback path is CI/locally verified.
- **2.2 Embedding stage (`embedding.py`)** — mirrors `fuzzy.py`; runs on post-Stage-3
  residuals; sentence-transformers + hnswlib, all guarded. Cap at review band
  (`min(0.89, cosine)`) unless city/postcode agrees; same corroboration adjustments;
  provenance `embedding`. Config `use_embedding=false`, `embedding_threshold`,
  `embedding_model`. **Optional deps, off by default.**

---

## Phase 3 — LLM adjudication of the review band (`adjudicate.py`)

anthropic SDK, key from `ANTHROPIC_API_KEY`, optional-guarded (feature hidden if
absent). Explicit user action only; batched (≤20/req), raw field values sent; strict
JSON contract parsed defensively; applied via the existing review overlay with
`source=llm` (human overrides; Undo works; reason shown). **Add an explicit data-egress
consent + cost hint before first run.** Config `llm_adjudication_enabled=false`,
`llm_model="claude-haiku-4-5-20251001"`, `llm_max_pairs=500`,
`llm_min_confidence_to_apply=0.8`. Model/SDK usage per the `claude-api` reference.

---

## Phase 4 — Deferred (spec-level; confirm before building)

4.1 one-to-one assignment · 4.2 threshold calibration from decisions · 4.3 dedupe.io
engine · 4.4 external registry enrichment. Not started without explicit go-ahead.

---

## Cross-cutting

- **Config plumbing:** `SessionConfig` (API) → `MatchConfig` (engine) →
  `service._to_match_config`; frontend `ConfigStep` "Advanced" panel; `api.ts` types.
- **Testing each phase:** new-normalizer unit tests; golden-file reconciliation +
  no-lost-MATCH regression; fallback-path tests (simulate missing postal / ST / anthropic);
  determinism (pinned seeds).
- **Deps:** `pycountry`, `phonenumbers`, `jellyfish` → default `requirements.txt`
  (Phase 1). `postal`, `sentence-transformers`, `hnswlib`, `anthropic` → optional
  `requirements-extra.txt`, lazy-imported, never required for a run.
- **Docs:** update `MATCHING_WORKFLOW.txt` after each phase; note optional installs in
  README/DEPLOY.
