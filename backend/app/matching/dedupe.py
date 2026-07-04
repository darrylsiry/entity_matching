"""Single-file deduplication pipeline.

Same cascade philosophy as the two-file linkage, but every stage compares rows of one
file against each other, and instead of best-per-record pairs the output is a
**clustering**: each row gets a `master_id`, and rows sharing a master_id are the same
entity. This is what lets a user upload one messy list and get a cross-link back to a
canonical id per duplicate group.

    raw
      -> normalize
      -> deterministic within  (exact id / domain)
      -> probabilistic within  (Splink dedupe_only)   [on rows not yet linked]
      -> fuzzy within          (rapidfuzz)             [on rows not yet linked]
      -> union all links -> connected components -> master_id per row
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import cluster, deterministic, fuzzy, probabilistic
from .normalize import normalize_frame
from .pipeline import MatchConfig, ProgressCb
from .schema import CanonicalField


@dataclass
class DedupeResult:
    clusters: pd.DataFrame   # row_idx, master_id, cluster_size
    norm: pd.DataFrame       # normalized frame (for optional display)
    counts: dict[str, int]


def _noop(_stage: str, _p: float) -> None:
    pass


def run_dedupe(
    raw: pd.DataFrame,
    mapping: dict[CanonicalField, str],
    config: MatchConfig,
    progress: ProgressCb | None = None,
) -> DedupeResult:
    progress = progress or _noop
    n = int(len(raw))

    progress("Normalizing records", 0.05)
    norm = normalize_frame(raw, mapping)

    links: list[pd.DataFrame] = []
    resolved: set[int] = set()  # rows already linked deterministically (strong signal)

    if config.use_deterministic:
        progress("Deterministic matching (IDs, domains)", 0.2)
        det = deterministic.match_within(norm)
        if not det.empty:
            links.append(det)
            resolved |= set(det["idx_a"].tolist()) | set(det["idx_b"].tolist())

    if config.use_probabilistic:
        progress("Probabilistic linkage (Splink)", 0.45)
        sub = norm.loc[~norm.index.isin(resolved)]
        if len(sub) >= 2:
            prob = probabilistic.match_probabilistic_within(
                sub, threshold=config.probabilistic_threshold
            )
            if not prob.empty:
                links.append(prob)
                strong = prob.loc[prob["score"] >= config.thresholds.match]
                resolved |= set(strong["idx_a"].tolist()) | set(strong["idx_b"].tolist())

    if config.use_fuzzy:
        progress("Fuzzy fallback (rapidfuzz)", 0.75)
        fz = fuzzy.match_fuzzy_within(
            norm, threshold=config.fuzzy_threshold, exclude=resolved
        )
        if not fz.empty:
            links.append(fz)

    progress("Clustering into master IDs", 0.9)
    all_links = (
        pd.concat(links, ignore_index=True)
        if links
        else pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])
    )
    clusters = cluster.assign_master_ids(n, all_links)

    num_clusters = int(clusters["master_id"].nunique()) if n else 0
    dup_rows = int((clusters["cluster_size"] > 1).sum())
    dup_clusters = (
        int(clusters.loc[clusters["cluster_size"] > 1, "master_id"].nunique())
        if n
        else 0
    )
    counts = {
        "records": n,
        "clusters": num_clusters,            # distinct entities after dedupe
        "duplicate_rows": dup_rows,          # rows that share an id with another row
        "duplicate_clusters": dup_clusters,  # entities that have >1 row
        "unique_rows": num_clusters - dup_clusters,  # entities with exactly one row
    }
    progress("Done", 1.0)
    return DedupeResult(clusters=clusters, norm=norm, counts=counts)
