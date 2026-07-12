"""Human-in-the-loop review for single-file dedupe ("possible duplicates").

Base clusters are built from CONFIDENT links only (score >= the Match threshold).
Review-band links (Review threshold <= score < Match threshold) are stored as
CANDIDATE pairs and are NOT merged automatically. The user confirms a candidate (which
merges the two rows' clusters) or dismisses it. Decisions are a small overlay applied
at read time; confirming re-runs the clustering with the extra link.

This mirrors review.py (two-file), but here a decision merges/keeps rows in a
clustering rather than accepting/rejecting a single A<->B pair.
"""

from __future__ import annotations

import pandas as pd

from . import storage
from .matching import cluster

CONFIRMED = "confirmed"
DISMISSED = "dismissed"


def pair_key(idx_a: int, idx_b: int) -> str:
    lo, hi = sorted((int(idx_a), int(idx_b)))
    return f"{lo}:{hi}"


def get_decisions(session_id: str) -> dict:
    return storage.load_decisions(session_id)


def set_candidate_decision(session_id: str, idx_a: int, idx_b: int, decision: str) -> None:
    """Confirm / dismiss / clear a possible-duplicate candidate."""
    cands = storage.load_candidates(session_id)
    key = pair_key(idx_a, idx_b)
    known = {pair_key(a, b) for a, b in zip(cands["idx_a"], cands["idx_b"], strict=False)}
    if key not in known:
        raise KeyError("no candidate pair for those rows")

    decisions = storage.load_decisions(session_id)
    if decision == "pending":
        decisions.pop(key, None)
    else:
        decisions[key] = decision
    storage.save_decisions(session_id, decisions)


def _base_links(base: pd.DataFrame) -> list[tuple[int, int]]:
    """Reconstruct connectivity from a base cluster frame (chain each group's rows)."""
    links: list[tuple[int, int]] = []
    for _, grp in base.groupby("master_id"):
        rows = sorted(int(r) for r in grp["row_idx"])
        links.extend((rows[i], rows[i + 1]) for i in range(len(rows) - 1))
    return links


def effective_clusters(session_id: str) -> pd.DataFrame:
    """Clusters from confident links PLUS any confirmed candidate links."""
    base = storage.load_result(session_id)
    n = int(len(base))
    if n == 0:
        return base
    links = _base_links(base)

    cands = storage.load_candidates(session_id)
    decisions = get_decisions(session_id)
    for a, b in zip(cands["idx_a"], cands["idx_b"], strict=False):
        if decisions.get(pair_key(a, b)) == CONFIRMED:
            links.append((int(a), int(b)))

    link_df = pd.DataFrame(links, columns=["idx_a", "idx_b"]) if links else \
        pd.DataFrame(columns=["idx_a", "idx_b"])
    return cluster.assign_master_ids(n, link_df)


def candidate_view(session_id: str) -> pd.DataFrame:
    """Candidate pairs annotated with their current decision (default pending)."""
    cands = storage.load_candidates(session_id).copy()
    if cands.empty:
        cands["decision"] = pd.Series(dtype="object")
        return cands
    decisions = get_decisions(session_id)
    cands["decision"] = [
        decisions.get(pair_key(a, b)) for a, b in zip(cands["idx_a"], cands["idx_b"], strict=False)
    ]
    return cands


def counts(session_id: str) -> dict:
    """Dedupe counts over the effective clustering, plus review tallies."""
    clusters = effective_clusters(session_id)
    n = int(len(clusters))
    if n == 0:
        return {"records": 0, "clusters": 0, "duplicate_rows": 0,
                "duplicate_clusters": 0, "unique_rows": 0,
                "possible_duplicates": 0, "confirmed": 0, "dismissed": 0}

    num_clusters = int(clusters["master_id"].nunique())
    dup_clusters = int(clusters.loc[clusters["cluster_size"] > 1, "master_id"].nunique())
    view = candidate_view(session_id)
    pending = int((view["decision"].isna()).sum()) if not view.empty else 0
    confirmed = int((view["decision"] == CONFIRMED).sum()) if not view.empty else 0
    dismissed = int((view["decision"] == DISMISSED).sum()) if not view.empty else 0
    return {
        "records": n,
        "clusters": num_clusters,
        "duplicate_rows": int((clusters["cluster_size"] > 1).sum()),
        "duplicate_clusters": dup_clusters,
        "unique_rows": num_clusters - dup_clusters,
        "possible_duplicates": pending,   # awaiting a decision
        "confirmed": confirmed,
        "dismissed": dismissed,
    }
