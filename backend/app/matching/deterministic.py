"""Stage 1 — deterministic matching.

Exact joins on the strongest identifiers, in priority order:
  1. registration / tax id
  2. website domain

These signals are near-certain, so pairs found here get a fixed high score and their
records are removed from the pool the expensive probabilistic stage has to consider.

A "pair" throughout the pipeline is a row with at least (idx_a, idx_b, score, stage).
idx_a / idx_b are the positional row indices into the two normalized frames.
"""

from __future__ import annotations

import pandas as pd

from .schema import NORM_DOMAIN, CanonicalField, norm_col

# Score assigned to a deterministic hit. Just under 1.0 so a later corroborating
# signal could in principle nudge it, but comfortably above any match threshold.
DETERMINISTIC_SCORE = 0.99

# (canonical column, human label) tried in order.
_KEYS = [
    (norm_col(CanonicalField.REGISTRATION_ID), "registration_id"),
    (NORM_DOMAIN, "domain"),
]


def _exact_join(a: pd.DataFrame, b: pd.DataFrame, key: str, rule: str) -> pd.DataFrame:
    """Inner-join two frames on a normalized key, ignoring empty keys."""
    left = a.loc[a[key] != "", [key]].rename_axis("idx_a").reset_index()
    right = b.loc[b[key] != "", [key]].rename_axis("idx_b").reset_index()
    if left.empty or right.empty:
        return _empty_pairs()
    merged = left.merge(right, on=key, how="inner")
    if merged.empty:
        return _empty_pairs()
    merged["score"] = DETERMINISTIC_SCORE
    merged["stage"] = "deterministic"
    merged["rule"] = rule
    return merged[["idx_a", "idx_b", "score", "stage", "rule"]]


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


def match_deterministic(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic pairs between normalized frames `a` and `b`.

    A given (idx_a, idx_b) is emitted once even if several keys agree; the first
    matching rule (highest priority) wins.
    """
    frames = [
        f
        for key, rule in _KEYS
        if key in a and key in b
        for f in [_exact_join(a, b, key, rule)]
        if not f.empty
    ]
    if not frames:
        return _empty_pairs()
    pairs = pd.concat(frames, ignore_index=True)
    return pairs.drop_duplicates(subset=["idx_a", "idx_b"], keep="first").reset_index(drop=True)
