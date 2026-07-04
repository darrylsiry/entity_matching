"""The cascade orchestrator — runs the matching stages "in a series".

    raw A, raw B
        -> normalize (stage 0)
        -> deterministic  (stage 1)   strong-id / domain exact
        -> probabilistic  (stage 2)   Splink, on records not yet resolved
        -> fuzzy fallback (stage 3)   rapidfuzz, on records still unresolved
        -> combine + classify (stage 4)

Each stage only sees the records earlier stages didn't confidently resolve, so the
expensive Splink step runs on a shrinking pool. A `progress` callback lets the async
job layer surface stage-by-stage status to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import deterministic, fuzzy, probabilistic
from .normalize import normalize_frame
from .scoring import (
    MATCH,
    REVIEW,
    UNIQUE_A,
    UNIQUE_B,
    Thresholds,
    combine_and_classify,
)
from .schema import CanonicalField

ProgressCb = Callable[[str, float], None]  # (stage label, progress 0..1)


@dataclass
class MatchConfig:
    """Toggles + thresholds chosen in the frontend's Configure step."""

    use_deterministic: bool = True
    use_probabilistic: bool = True
    use_fuzzy: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds)
    fuzzy_threshold: float = 0.80
    probabilistic_threshold: float = 0.75


@dataclass
class MatchResult:
    pairs: pd.DataFrame                 # classified best-per-A match/review pairs
    unique_a_idx: list[int]            # A rows with no counterpart
    unique_b_idx: list[int]            # B rows with no counterpart
    norm_a: pd.DataFrame               # normalized frame A (for result rendering)
    norm_b: pd.DataFrame               # normalized frame B
    counts: dict[str, int]             # summary counts for the UI

    def storage_frame(self) -> pd.DataFrame:
        """Matched pairs + single-sided unique rows — the reconciled frame that the API
        serves and exports. Each file reconciles: matched + review + unique == rows."""
        unique = _unique_frame(self.unique_a_idx, self.unique_b_idx)
        if unique.empty:
            return self.pairs
        return pd.concat([self.pairs, unique], ignore_index=True)


def _noop(_stage: str, _p: float) -> None:
    pass


def run_pipeline(
    raw_a: pd.DataFrame,
    raw_b: pd.DataFrame,
    mapping_a: dict[CanonicalField, str],
    mapping_b: dict[CanonicalField, str],
    config: MatchConfig,
    progress: ProgressCb | None = None,
) -> MatchResult:
    """Run the full cascade. Each file has its own header->canonical mapping; both
    normalize into the shared canonical schema before matching."""
    progress = progress or _noop

    progress("Normalizing records", 0.05)
    norm_a = normalize_frame(raw_a, mapping_a)
    norm_b = normalize_frame(raw_b, mapping_b)

    all_pairs: list[pd.DataFrame] = []
    resolved_a: set[int] = set()  # A indices confidently matched, excluded downstream
    resolved_b: set[int] = set()

    if config.use_deterministic:
        progress("Deterministic matching (IDs, domains)", 0.2)
        det = deterministic.match_deterministic(norm_a, norm_b)
        if not det.empty:
            all_pairs.append(det)
            resolved_a |= set(det["idx_a"].tolist())
            resolved_b |= set(det["idx_b"].tolist())

    if config.use_probabilistic:
        progress("Probabilistic linkage (Splink)", 0.45)
        # Run Splink on records not already deterministically resolved.
        sub_a = norm_a.loc[~norm_a.index.isin(resolved_a)]
        sub_b = norm_b.loc[~norm_b.index.isin(resolved_b)]
        if not sub_a.empty and not sub_b.empty:
            prob = probabilistic.match_probabilistic(
                sub_a, sub_b, threshold=config.probabilistic_threshold
            )
            if not prob.empty:
                all_pairs.append(prob)
                # Only treat high-confidence probabilistic pairs as "resolved" so the
                # fuzzy fallback still gets a shot at the uncertain middle band.
                strong = prob.loc[prob["score"] >= config.thresholds.match]
                resolved_a |= set(strong["idx_a"].tolist())
                resolved_b |= set(strong["idx_b"].tolist())

    if config.use_fuzzy:
        progress("Fuzzy fallback (rapidfuzz)", 0.75)
        fz = fuzzy.match_fuzzy(
            norm_a,
            norm_b,
            threshold=config.fuzzy_threshold,
            exclude_a=resolved_a,
            exclude_b=resolved_b,
        )
        if not fz.empty:
            all_pairs.append(fz)

    progress("Scoring and classifying", 0.9)
    combined = (
        pd.concat(all_pairs, ignore_index=True)
        if all_pairs
        else pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])
    )
    classified = combine_and_classify(combined, config.thresholds)

    # Unique records: rows from each file that ended up in no match/review pair. These
    # make the categories reconcile — for each file, matched + review + unique == rows.
    n_a, n_b = int(len(raw_a)), int(len(raw_b))
    matched_a = set(classified["idx_a"].tolist())
    matched_b = set(classified["idx_b"].tolist())
    unique_a_idx = [i for i in range(n_a) if i not in matched_a]
    unique_b_idx = [i for i in range(n_b) if i not in matched_b]

    n_match = int((classified["match_class"] == MATCH).sum())
    n_review = int((classified["match_class"] == REVIEW).sum())
    counts = {
        "records_a": n_a,
        "records_b": n_b,
        MATCH: n_match,           # A-side: pairs are unique per A record
        REVIEW: n_review,
        UNIQUE_A: len(unique_a_idx),
        "matched_b": len(matched_b),   # distinct B records with a counterpart
        UNIQUE_B: len(unique_b_idx),
        "total_pairs": int(len(classified)),
    }
    progress("Done", 1.0)
    return MatchResult(
        pairs=classified,
        unique_a_idx=unique_a_idx,
        unique_b_idx=unique_b_idx,
        norm_a=norm_a,
        norm_b=norm_b,
        counts=counts,
    )


def _unique_frame(unique_a_idx: list[int], unique_b_idx: list[int]) -> pd.DataFrame:
    """Build single-sided rows for unmatched records (sentinel -1 on the empty side)."""
    rows = [
        {"idx_a": i, "idx_b": -1, "score": 0.0, "stage": "", "rule": "",
         "match_class": UNIQUE_A}
        for i in unique_a_idx
    ] + [
        {"idx_a": -1, "idx_b": i, "score": 0.0, "stage": "", "rule": "",
         "match_class": UNIQUE_B}
        for i in unique_b_idx
    ]
    cols = ["idx_a", "idx_b", "score", "stage", "rule", "match_class"]
    return pd.DataFrame(rows, columns=cols)
