"""Stage 4 — combine and classify.

Takes candidate pairs from every stage, resolves each A record to its single best B
match, and classifies by score into match / review / no_match bands.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MATCH = "match"
REVIEW = "review"
NO_MATCH = "no_match"
# Single-file records with no counterpart in the other file. Used so the result
# categories reconcile against each file's total row count.
UNIQUE_A = "unique_a"
UNIQUE_B = "unique_b"


@dataclass(frozen=True)
class Thresholds:
    """Score bands. score >= match -> match; >= review -> review; else no_match."""

    match: float = 0.90
    review: float = 0.75

    def classify(self, score: float) -> str:
        if score >= self.match:
            return MATCH
        if score >= self.review:
            return REVIEW
        return NO_MATCH


def combine_and_classify(
    pairs: pd.DataFrame, thresholds: Thresholds
) -> pd.DataFrame:
    """Deduplicate to best-per-A-record and add a `match_class` column.

    Input pairs may contain the same idx_a from multiple stages; the highest score
    wins (deterministic hits at 0.99 naturally dominate). Only pairs that reach at
    least the review band are returned.
    """
    cols = ["idx_a", "idx_b", "score", "stage", "rule", "match_class"]
    if pairs is None or pairs.empty:
        return pd.DataFrame(columns=cols)

    ranked = pairs.sort_values("score", ascending=False)
    best = ranked.drop_duplicates(subset=["idx_a"], keep="first").copy()

    best["match_class"] = best["score"].map(thresholds.classify)
    best = best[best["match_class"] != NO_MATCH]
    return best.sort_values("score", ascending=False).reset_index(drop=True)[cols]
