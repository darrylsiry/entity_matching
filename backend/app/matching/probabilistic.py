"""Stage 2 — probabilistic record linkage with Splink (Fellegi-Sunter, DuckDB backend).

This is the workhorse for scale. Instead of comparing all candidate pairs it uses
**blocking rules** (name tokens, postcode, domain) to generate only plausible pairs,
then scores each with a probabilistic model over multiple field comparisons
(Jaro-Winkler name, exact domain, Levenshtein address, exact postcode).

Two entry points share one implementation:
  - match_probabilistic(a, b)      link two files       (link_type="link_only")
  - match_probabilistic_within(a)  dedupe one file      (link_type="dedupe_only")

Model parameters are learned unsupervised: `u` from random sampling and `m` via
expectation-maximisation. On very small or low-variety inputs EM can fail to
converge; the whole stage is therefore defensive — any failure (including Splink not
being installed) returns an empty result so the pipeline falls back to the fuzzy
stage rather than crashing.
"""

from __future__ import annotations

import logging

import pandas as pd

from .schema import (
    NORM_ADDR_HOUSE_NUMBER,
    NORM_ADDR_ROAD,
    NORM_DOMAIN,
    NORM_FIRST_METAPHONE,
    NORM_NAME_TOKENS,
    CanonicalField,
    norm_col,
)

logger = logging.getLogger(__name__)

_NAME = norm_col(CanonicalField.NAME)
_ADDRESS = norm_col(CanonicalField.ADDRESS)
_CITY = norm_col(CanonicalField.CITY)
_POSTCODE = norm_col(CanonicalField.POSTCODE)
_PHONE = norm_col(CanonicalField.PHONE)


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Input prep — Splink requires a unique_id column per frame
# ══════════════════════════════════════════════════════════════════════════


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    """Splink needs a unique_id column; use the positional index."""
    out = df.copy()
    out["unique_id"] = out.index.astype("int64")
    return out


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Entry points — thin wrappers over one shared implementation
# match_probabilistic: two files (link_only, only cross-file pairs).
# match_probabilistic_within: one file (dedupe_only, within-file pairs).
# ══════════════════════════════════════════════════════════════════════════


def match_probabilistic(
    a: pd.DataFrame,
    b: pd.DataFrame,
    threshold: float = 0.75,
    *,
    use_phone: bool = True,
    phonetic_blocking: bool = True,
    tf_adjustments: bool = True,
) -> pd.DataFrame:
    """Cross-file pairs (idx_a in A, idx_b in B) with match_probability >= threshold."""
    return _run(
        frames=[_prep(a), _prep(b)],
        aliases=["a", "b"],
        link_type="link_only",
        has_signal=lambda col: _both_have(a, b, col),
        threshold=threshold,
        orient=_orient_link_only,
        use_phone=use_phone,
        phonetic_blocking=phonetic_blocking,
        tf_adjustments=tf_adjustments,
    )


def match_probabilistic_within(
    a: pd.DataFrame,
    threshold: float = 0.75,
    *,
    use_phone: bool = True,
    phonetic_blocking: bool = True,
    tf_adjustments: bool = True,
) -> pd.DataFrame:
    """Within-file pairs (both idx_a and idx_b index the same frame) for dedupe."""
    return _run(
        frames=[_prep(a)],
        aliases=["a"],
        link_type="dedupe_only",
        has_signal=lambda col: _one_has(a, col),
        threshold=threshold,
        orient=_orient_dedupe,
        use_phone=use_phone,
        phonetic_blocking=phonetic_blocking,
        tf_adjustments=tf_adjustments,
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Shared implementation — build the Splink model and run it
# Flow inside _run():
#   1. choose BLOCKING rules from columns that carry signal (empties excluded)
#   2. choose field COMPARISONS (name JW + TF, domain/phone/postcode exact, ...)
#   3. unsupervised TRAINING (u by random sampling, m by EM per blocking rule)
#   4. PREDICT pairs >= threshold and map Splink ids back to row indices
# Everything is wrapped defensively: any failure logs a warning and returns an
# empty frame so the cascade falls through to the fuzzy stage instead of dying.
# ══════════════════════════════════════════════════════════════════════════


def _both_have(a: pd.DataFrame, b: pd.DataFrame, col: str) -> bool:
    return col in a and col in b and (a[col] != "").any() and (b[col] != "").any()


def _one_has(a: pd.DataFrame, col: str) -> bool:
    return col in a and (a[col] != "").any()


def _run(
    frames,
    aliases,
    link_type,
    has_signal,
    threshold,
    orient,
    use_phone=True,
    phonetic_blocking=True,
    tf_adjustments=True,
) -> pd.DataFrame:
    try:
        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("Splink unavailable, skipping probabilistic stage: %s", exc)
        return _empty_pairs()

    try:
        # Block only on columns that carry signal. Critically, wrap each key in
        # NULLIF(col, '') so *empty* values don't join: block_on generates
        # `l.col = r.col`, and since NULL != NULL in SQL, all the blank-value rows
        # (e.g. the ~80% with no website) are NOT lumped into one giant candidate
        # block. Without this, every website-less pair gets compared and the
        # unsupervised model scores many unrelated companies at ~0.99.
        def block_nonempty(col: str):
            return block_on(f"NULLIF({col}, '')")

        def with_tf(comparison):
            """Enable term-frequency adjustments, falling back untouched on failure.

            TF makes agreement on a *rare* value count for more than agreement on a
            common one ("Zyxwv Corp" >> "Global Services"). Guarded so an API change
            in Splink degrades to the plain comparison instead of killing the stage.
            """
            if not tf_adjustments:
                return comparison
            try:
                return comparison.configure(term_frequency_adjustments=True)
            except Exception as exc:  # pragma: no cover - version drift guard
                logger.info("TF adjustments unavailable for %s: %s", comparison, exc)
                return comparison

        blocking_rules = []
        if has_signal(NORM_NAME_TOKENS):
            blocking_rules.append(block_nonempty(NORM_NAME_TOKENS))
        if has_signal(_POSTCODE):
            blocking_rules.append(block_nonempty(_POSTCODE))
        if has_signal(NORM_DOMAIN):
            blocking_rules.append(block_nonempty(NORM_DOMAIN))
        if use_phone and has_signal(_PHONE):
            blocking_rules.append(block_nonempty(_PHONE))
        if phonetic_blocking and has_signal(NORM_FIRST_METAPHONE):
            # Phonetic key survives typos in the first characters ("Jonson"/"Johnson").
            blocking_rules.append(block_nonempty(NORM_FIRST_METAPHONE))
        if not blocking_rules:
            blocking_rules.append(block_on(f"substr({_NAME}, 1, 4)"))

        comparisons = []
        if has_signal(_NAME):
            comparisons.append(with_tf(cl.JaroWinklerAtThresholds(_NAME, [0.9, 0.8])))
        if has_signal(NORM_DOMAIN):
            comparisons.append(cl.ExactMatch(NORM_DOMAIN))
        if use_phone and has_signal(_PHONE):
            comparisons.append(cl.ExactMatch(_PHONE))
        if has_signal(NORM_ADDR_ROAD):
            # libpostal parsed the addresses: compare structured components so
            # "123 Main St Ste 4" vs "123 Main Street" agrees on the road instead
            # of the whole-string edit distance reading it as disagreement.
            if has_signal(NORM_ADDR_HOUSE_NUMBER):
                comparisons.append(cl.ExactMatch(NORM_ADDR_HOUSE_NUMBER))
            comparisons.append(cl.LevenshteinAtThresholds(NORM_ADDR_ROAD, [2, 5]))
        elif has_signal(_ADDRESS):
            comparisons.append(cl.LevenshteinAtThresholds(_ADDRESS, [2, 5]))
        if has_signal(_POSTCODE):
            comparisons.append(cl.ExactMatch(_POSTCODE))
        if has_signal(_CITY):
            comparisons.append(with_tf(cl.ExactMatch(_CITY)))
        if not comparisons:
            return _empty_pairs()

        settings = SettingsCreator(
            link_type=link_type,
            blocking_rules_to_generate_predictions=blocking_rules,
            comparisons=comparisons,
            retain_intermediate_calculation_columns=False,
        )

        db_api = DuckDBAPI()
        linker = Linker(
            frames, settings, db_api=db_api, input_table_aliases=aliases
        )

        linker.training.estimate_probability_two_random_records_match(
            blocking_rules, recall=0.7
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000)
        for br in blocking_rules:
            try:
                linker.training.estimate_parameters_using_expectation_maximisation(br)
            except Exception as exc:  # EM can fail on a given rule; keep going.
                logger.info("EM training skipped for a blocking rule: %s", exc)

        predictions = linker.inference.predict(threshold_match_probability=threshold)
        pred = predictions.as_pandas_dataframe()
    except Exception as exc:
        logger.warning("Probabilistic stage failed, falling back: %s", exc)
        return _empty_pairs()

    if pred is None or pred.empty:
        return _empty_pairs()
    return orient(pred)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Output shaping — map Splink's predictions back to (idx_a, idx_b)
# pairs in the pipeline's standard schema.
# ══════════════════════════════════════════════════════════════════════════


def _orient_link_only(pred: pd.DataFrame) -> pd.DataFrame:
    """link_only produces cross-dataset pairs; normalize so idx_a is the 'a' side."""
    left_is_a = pred["source_dataset_l"] == "a"
    idx_a = pred["unique_id_l"].where(left_is_a, pred["unique_id_r"]).astype("int64")
    idx_b = pred["unique_id_r"].where(left_is_a, pred["unique_id_l"]).astype("int64")
    return _pairs_frame(idx_a.values, idx_b.values, pred["match_probability"])


def _orient_dedupe(pred: pd.DataFrame) -> pd.DataFrame:
    """dedupe_only produces within-file pairs; both ids index the same frame."""
    return _pairs_frame(
        pred["unique_id_l"].astype("int64").values,
        pred["unique_id_r"].astype("int64").values,
        pred["match_probability"],
    )


def _pairs_frame(idx_a, idx_b, score) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "idx_a": idx_a,
            "idx_b": idx_b,
            "score": pd.Series(score).round(4).values,
            "stage": "probabilistic",
            "rule": "splink",
        }
    ).reset_index(drop=True)
