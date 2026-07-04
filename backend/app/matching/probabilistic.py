"""Stage 2 — probabilistic record linkage with Splink (Fellegi-Sunter, DuckDB backend).

This is the workhorse for scale. Instead of comparing all A x B pairs it uses
**blocking rules** (name tokens, postcode, domain) to generate only plausible
candidate pairs, then scores each with a probabilistic model over multiple field
comparisons (Jaro-Winkler name, exact domain, Levenshtein address, exact postcode).

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
    NORM_DOMAIN,
    NORM_NAME_TOKENS,
    CanonicalField,
    norm_col,
)

logger = logging.getLogger(__name__)

_NAME = norm_col(CanonicalField.NAME)
_ADDRESS = norm_col(CanonicalField.ADDRESS)
_CITY = norm_col(CanonicalField.CITY)
_POSTCODE = norm_col(CanonicalField.POSTCODE)


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    """Splink needs a unique_id column; use the positional index."""
    out = df.copy()
    out["unique_id"] = out.index.astype("int64")
    return out


def match_probabilistic(
    a: pd.DataFrame,
    b: pd.DataFrame,
    threshold: float = 0.75,
) -> pd.DataFrame:
    """Return probabilistic cross-file pairs with match_probability >= threshold.

    Never raises: on any error it logs and returns an empty frame.
    """
    try:
        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("Splink unavailable, skipping probabilistic stage: %s", exc)
        return _empty_pairs()

    try:
        # Only block on / compare columns that actually carry signal in both frames.
        blocking_rules = []
        comparisons = []

        def both_have(col: str) -> bool:
            return (
                col in a and col in b
                and (a[col] != "").any() and (b[col] != "").any()
            )

        if both_have(NORM_NAME_TOKENS):
            blocking_rules.append(block_on(NORM_NAME_TOKENS))
        if both_have(_POSTCODE):
            blocking_rules.append(block_on(_POSTCODE))
        if both_have(NORM_DOMAIN):
            blocking_rules.append(block_on(NORM_DOMAIN))
        if not blocking_rules:
            # Fall back to blocking on the first name token to stay tractable.
            blocking_rules.append(block_on(f"substr({_NAME}, 1, 4)"))

        if both_have(_NAME):
            comparisons.append(cl.JaroWinklerAtThresholds(_NAME, [0.9, 0.8]))
        if both_have(NORM_DOMAIN):
            comparisons.append(cl.ExactMatch(NORM_DOMAIN))
        if both_have(_ADDRESS):
            comparisons.append(cl.LevenshteinAtThresholds(_ADDRESS, [2, 5]))
        if both_have(_POSTCODE):
            comparisons.append(cl.ExactMatch(_POSTCODE))
        if both_have(_CITY):
            comparisons.append(cl.ExactMatch(_CITY))

        if not comparisons:
            return _empty_pairs()

        settings = SettingsCreator(
            link_type="link_only",
            blocking_rules_to_generate_predictions=blocking_rules,
            comparisons=comparisons,
            retain_intermediate_calculation_columns=False,
        )

        db_api = DuckDBAPI()
        linker = Linker(
            [_prep(a), _prep(b)],
            settings,
            db_api=db_api,
            input_table_aliases=["a", "b"],
        )

        # Unsupervised parameter estimation.
        linker.training.estimate_probability_two_random_records_match(
            blocking_rules, recall=0.7
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000)
        for br in blocking_rules:
            try:
                linker.training.estimate_parameters_using_expectation_maximisation(br)
            except Exception as exc:  # EM can fail on a given rule; keep going.
                logger.info("EM training skipped for a blocking rule: %s", exc)

        predictions = linker.inference.predict(
            threshold_match_probability=threshold
        )
        pred = predictions.as_pandas_dataframe()
    except Exception as exc:
        logger.warning("Probabilistic stage failed, falling back: %s", exc)
        return _empty_pairs()

    if pred is None or pred.empty:
        return _empty_pairs()

    # Map Splink's (source_dataset, unique_id) back to our A/B positional indices.
    # link_only only produces cross-dataset pairs; normalize orientation to a=left.
    left_is_a = pred["source_dataset_l"] == "a"
    idx_a = pred["unique_id_l"].where(left_is_a, pred["unique_id_r"]).astype("int64")
    idx_b = pred["unique_id_r"].where(left_is_a, pred["unique_id_l"]).astype("int64")

    out = pd.DataFrame(
        {
            "idx_a": idx_a.values,
            "idx_b": idx_b.values,
            "score": pred["match_probability"].round(4).values,
            "stage": "probabilistic",
            "rule": "splink",
        }
    )
    return out.reset_index(drop=True)
