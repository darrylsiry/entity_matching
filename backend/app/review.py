"""Human-in-the-loop review decisions for two-file matching.

A `review`-class pair is uncertain. The user can **confirm** it (it becomes a match in
the final dataset) or **dismiss** it (not a match — the two records revert to unique).

Decisions are stored as a tiny sidecar (pair-key -> "confirmed"|"dismissed") and applied
as an *overlay* over the stored base result frame at read time, so a click never
rewrites the big parquet. Everything the API serves in link mode — results, counts and
the CSV export — goes through the effective frame below, so the dataset always reflects
the user's decisions.
"""

from __future__ import annotations

import pandas as pd

from . import storage
from .matching.pipeline import _unique_frame
from .matching.scoring import MATCH, REVIEW, UNIQUE_A, UNIQUE_B

CONFIRMED = "confirmed"
DISMISSED = "dismissed"
_PAIR_CLASSES = [MATCH, REVIEW]
_COLS = [
    "idx_a", "idx_b", "score", "stage", "rule", "adjustments",
    "match_class", "decision",
]


def _ensure_cols(frame: pd.DataFrame) -> pd.DataFrame:
    """Backfill columns older stored results may lack (e.g. adjustments)."""
    if "adjustments" not in frame.columns:
        frame = frame.copy()
        frame["adjustments"] = ""
    return frame


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Decision store — record confirm/dismiss/undo on a review pair
# Decisions live in a tiny JSON sidecar keyed "idxA:idxB"; only pairs that are
# actually in the REVIEW band can be decided.
# ══════════════════════════════════════════════════════════════════════════


def pair_key(idx_a: int, idx_b: int) -> str:
    return f"{int(idx_a)}:{int(idx_b)}"


def get_decisions(session_id: str) -> dict:
    return storage.load_decisions(session_id)


def set_decision(session_id: str, idx_a: int, idx_b: int, decision: str) -> None:
    """Record (or clear, with "pending") a decision on a review pair."""
    base = storage.load_result(session_id)
    is_review = (
        (base["match_class"] == REVIEW)
        & (base["idx_a"] == idx_a)
        & (base["idx_b"] == idx_b)
    )
    if not is_review.any():
        raise KeyError("no review pair for those indices")

    decisions = storage.load_decisions(session_id)
    key = pair_key(idx_a, idx_b)
    if decision == "pending":
        decisions.pop(key, None)
    else:
        decisions[key] = decision
    storage.save_decisions(session_id, decisions)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Effective view — the stored results with decisions applied
# confirmed review → match; dismissed review → dropped (its records fall back
# to "unique"); pending → still review. Unique rows are recomputed so the
# per-file reconciliation (matched + review + unique = rows) always holds.
# Everything the API serves (results, counts, CSV) reads THIS view.
# ══════════════════════════════════════════════════════════════════════════


def _all_indices(base: pd.DataFrame) -> tuple[set[int], set[int]]:
    """Every A and B row index that exists across the base frame."""
    pairs = base[base["match_class"].isin(_PAIR_CLASSES)]
    all_a = set(pairs["idx_a"].astype(int)) | set(
        base.loc[base["match_class"] == UNIQUE_A, "idx_a"].astype(int)
    )
    all_b = set(pairs["idx_b"].astype(int)) | set(
        base.loc[base["match_class"] == UNIQUE_B, "idx_b"].astype(int)
    )
    all_a.discard(-1)
    all_b.discard(-1)
    return all_a, all_b


def effective_frame(session_id: str) -> pd.DataFrame:
    """Base frame with decisions applied and unique rows recomputed.

    - confirmed review -> match (decision="confirmed")
    - dismissed review -> dropped; its records fall back into unique_a / unique_b
    - pending review    -> review
    """
    base = _ensure_cols(storage.load_result(session_id))
    if base.empty:
        return pd.DataFrame(columns=_COLS)
    decisions = get_decisions(session_id)

    pairs = base[base["match_class"].isin(_PAIR_CLASSES)].copy()
    eff_class: list[str] = []
    decision_col: list[str | None] = []
    for r in pairs.itertuples(index=False):
        if r.match_class == REVIEW:
            d = decisions.get(pair_key(r.idx_a, r.idx_b))
            if d == CONFIRMED:
                eff_class.append(MATCH)
                decision_col.append(CONFIRMED)
            elif d == DISMISSED:
                eff_class.append("__drop__")
                decision_col.append(DISMISSED)
            else:
                eff_class.append(REVIEW)
                decision_col.append(None)
        else:
            eff_class.append(MATCH)
            decision_col.append(None)
    pairs["match_class"] = eff_class
    pairs["decision"] = decision_col

    active = pairs[pairs["match_class"].isin(_PAIR_CLASSES)]

    all_a, all_b = _all_indices(base)
    matched_a = set(active["idx_a"].astype(int))
    matched_b = set(active["idx_b"].astype(int))
    unique = _unique_frame(sorted(all_a - matched_a), sorted(all_b - matched_b))
    if unique.empty:
        return active[_COLS].reset_index(drop=True)
    unique["decision"] = None
    return pd.concat([active[_COLS], unique[_COLS]], ignore_index=True)


def dismissed_frame(session_id: str) -> pd.DataFrame:
    """Review pairs the user dismissed — an audit/undo view (not part of counts)."""
    base = _ensure_cols(storage.load_result(session_id))
    decisions = get_decisions(session_id)
    review = base[base["match_class"] == REVIEW].copy()
    if review.empty:
        return pd.DataFrame(columns=_COLS)
    mask = [
        decisions.get(pair_key(a, b)) == DISMISSED
        for a, b in zip(review["idx_a"], review["idx_b"], strict=False)
    ]
    out = review[mask].copy()
    out["match_class"] = DISMISSED
    out["decision"] = DISMISSED
    return out[_COLS]


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Counts — reconciling per-file totals + review-workflow tallies
# (confirmed/dismissed), all derived from the effective view above.
# ══════════════════════════════════════════════════════════════════════════


def effective_counts(session_id: str) -> dict:
    """Reconciling counts over the effective frame, plus review-workflow tallies."""
    eff = effective_frame(session_id)
    decisions = get_decisions(session_id)
    if eff.empty:
        return {"records_a": 0, "records_b": 0, "match": 0, "review": 0,
                "matched_b": 0, "unique_a": 0, "unique_b": 0, "pairs": 0,
                "confirmed": 0, "dismissed": 0, "total": 0}

    vc = eff["match_class"].value_counts().to_dict()
    match = int(vc.get(MATCH, 0))
    review = int(vc.get(REVIEW, 0))
    unique_a = int(vc.get(UNIQUE_A, 0))
    unique_b = int(vc.get(UNIQUE_B, 0))
    pair_rows = eff[eff["match_class"].isin(_PAIR_CLASSES)]
    matched_b = int(pair_rows["idx_b"].nunique()) if not pair_rows.empty else 0
    confirmed = int((eff["decision"] == CONFIRMED).sum())
    dismissed = sum(1 for v in decisions.values() if v == DISMISSED)
    return {
        "records_a": match + review + unique_a,
        "records_b": matched_b + unique_b,
        "match": match,
        "review": review,
        "matched_b": matched_b,
        "unique_a": unique_a,
        "unique_b": unique_b,
        "pairs": match + review,
        "confirmed": confirmed,
        "dismissed": dismissed,
        "total": int(len(eff)),
    }
