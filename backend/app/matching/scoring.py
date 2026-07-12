"""Stage 4 — combine and classify.

Takes candidate pairs from every stage, resolves each A record to its single best B
match, and classifies by score into match / review / no_match bands. Classification
can be overridden per stage (scores from different stages sit on different scales).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Classification labels + the shared pair schema
# These string constants and PAIR_COLS are the vocabulary every stage and the
# API speak; change them here and everything downstream follows.
# ══════════════════════════════════════════════════════════════════════════

MATCH = "match"
REVIEW = "review"
NO_MATCH = "no_match"
# Single-file records with no counterpart in the other file. Used so the result
# categories reconcile against each file's total row count.
UNIQUE_A = "unique_a"
UNIQUE_B = "unique_b"

# Canonical schema for stage-emitted pairs. `adjustments` is a comma-joined list of
# corroboration signals applied to the score (provenance for the review UI/CSV).
PAIR_COLS = ["idx_a", "idx_b", "score", "stage", "rule", "adjustments"]

# stage name -> (match_override | None, review_override | None)
StageThresholds = dict[str, tuple[float | None, float | None]]


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Thresholds — the score bands, with optional per-stage overrides
# Scores from different stages sit on different scales (deterministic 0.99,
# Splink probability, fuzzy adjusted ratio), so classify_pair lets a specific
# stage carry its own match/review cutoffs; unset values inherit the globals.
# ══════════════════════════════════════════════════════════════════════════


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


def classify_pair(
    score: float,
    stage: str,
    thresholds: Thresholds,
    stage_thresholds: StageThresholds | None = None,
) -> str:
    """Classify one pair, honoring any per-stage threshold override (None inherits)."""
    match_t, review_t = thresholds.match, thresholds.review
    if stage_thresholds and stage in stage_thresholds:
        m_over, r_over = stage_thresholds[stage]
        if m_over is not None:
            match_t = m_over
        if r_over is not None:
            review_t = r_over
    if score >= match_t:
        return MATCH
    if score >= review_t:
        return REVIEW
    return NO_MATCH


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Stage 4 (link mode) — combine all stages' pairs and classify
# Dedup to the single best pair per A record (highest score wins), band each
# by threshold, and drop everything below the review band.
# ══════════════════════════════════════════════════════════════════════════


def combine_and_classify(
    pairs: pd.DataFrame,
    thresholds: Thresholds,
    stage_thresholds: StageThresholds | None = None,
) -> pd.DataFrame:
    """Deduplicate to best-per-A-record and add a `match_class` column.

    Input pairs may contain the same idx_a from multiple stages; the highest score
    wins (deterministic hits at 0.99 naturally dominate). Only pairs that reach at
    least the review band are returned.
    """
    cols = [*PAIR_COLS, "match_class"]
    if pairs is None or pairs.empty:
        return pd.DataFrame(columns=cols)

    ranked = pairs.copy()
    if "adjustments" not in ranked.columns:
        ranked["adjustments"] = ""
    ranked = ranked.sort_values("score", ascending=False)
    best = ranked.drop_duplicates(subset=["idx_a"], keep="first").copy()

    best["match_class"] = [
        classify_pair(float(s), str(st), thresholds, stage_thresholds)
        for s, st in zip(best["score"], best["stage"], strict=False)
    ]
    best = best[best["match_class"] != NO_MATCH]
    return best.sort_values("score", ascending=False).reset_index(drop=True)[cols]
