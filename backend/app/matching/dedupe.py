"""Single-file deduplication pipeline.

Same cascade philosophy as the two-file linkage, but every stage compares rows of one
file against each other, and the output is a **clustering**: each row gets a
`master_id`, and rows sharing a master_id are the same entity.

Like link mode, links are split by confidence:
  - CONFIDENT links (score >= the Match threshold, default 0.90) auto-merge rows into
    the base clusters.
  - CANDIDATE links (Review band: review threshold <= score < Match threshold) are NOT
    auto-merged. They're surfaced as "possible duplicates" for the user to confirm
    (merge) or dismiss — mirroring the two-file review queue.

    raw
      -> normalize
      -> deterministic within  (exact id / domain)         [always confident]
      -> probabilistic within  (Splink dedupe_only)
      -> fuzzy within          (rapidfuzz)
      -> split links by score  -> confident components = master_id per row
                               -> review-band = possible-duplicate candidates
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import cluster, deterministic, fuzzy, probabilistic
from .normalize import normalize_frame
from .pipeline import MatchConfig, ProgressCb
from .schema import CanonicalField

_LINK_COLS = ["idx_a", "idx_b", "score", "stage", "rule", "adjustments"]


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Result container
# clusters = master IDs from CONFIDENT links only; candidates = review-band
# "possible duplicate" pairs awaiting a human confirm/dismiss.
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class DedupeResult:
    clusters: pd.DataFrame   # row_idx, master_id, cluster_size (from confident links)
    candidates: pd.DataFrame  # review-band pairs between different clusters (idx_a<idx_b)
    norm: pd.DataFrame       # normalized frame (for optional display)
    counts: dict[str, int]


def _noop(_stage: str, _p: float) -> None:
    pass


# ══════════════════════════════════════════════════════════════════════════
# SECTION: The dedupe cascade (single-file)
# Same stages as link mode but every comparison is within one file. Links are
# then split by confidence: >= Match threshold auto-merge into clusters;
# review-band links become "possible duplicate" candidates for the user.
# ══════════════════════════════════════════════════════════════════════════


def run_dedupe(
    raw: pd.DataFrame,
    mapping: dict[CanonicalField, str],
    config: MatchConfig,
    progress: ProgressCb | None = None,
) -> DedupeResult:
    progress = progress or _noop
    n = int(len(raw))

    progress("Normalizing records", 0.05)
    norm = normalize_frame(raw, mapping, phone_region=config.default_phone_region)

    links: list[pd.DataFrame] = []
    resolved: set[int] = set()  # rows already linked deterministically (strong signal)

    if config.use_deterministic:
        progress("Deterministic matching (IDs, domains, phones)", 0.2)
        det = deterministic.match_within(
            norm,
            use_phone=config.use_phone,
            corroboration=config.deterministic_corroboration,
            name_floor=config.deterministic_name_floor,
            demoted_score=config.deterministic_demoted_score,
        )
        if not det.empty:
            links.append(det)
            # Corroboration-demoted pairs (review band) stay in the pool.
            strong = det.loc[det["score"] >= config.thresholds.match]
            resolved |= set(strong["idx_a"].tolist()) | set(strong["idx_b"].tolist())

    if config.use_probabilistic:
        progress("Probabilistic linkage (Splink)", 0.45)
        # Only exclude rows CONFIDENTLY resolved deterministically; a row can still
        # gain a review-band candidate link.
        sub = norm.loc[~norm.index.isin(resolved)]
        if len(sub) >= 2:
            prob = probabilistic.match_probabilistic_within(
                sub,
                threshold=config.probabilistic_threshold,
                use_phone=config.use_phone,
                phonetic_blocking=config.use_phonetic_blocking,
                tf_adjustments=config.splink_tf_adjustments,
            )
            if not prob.empty:
                links.append(prob)
                strong = prob.loc[prob["score"] >= config.thresholds.match]
                resolved |= set(strong["idx_a"].tolist()) | set(strong["idx_b"].tolist())

    if config.use_fuzzy:
        progress("Fuzzy fallback (rapidfuzz)", 0.75)
        fz = fuzzy.match_fuzzy_within(
            norm,
            threshold=config.fuzzy_threshold,
            exclude=resolved,
            phonetic=config.use_phonetic_blocking,
        )
        if not fz.empty:
            links.append(fz)

    progress("Clustering into master IDs", 0.9)
    all_links = (
        pd.concat([_with_link_cols(f) for f in links], ignore_index=True)
        if links
        else _empty_links()
    )
    all_links = _normalize_pairs(all_links)

    # Split by confidence: confident links auto-cluster; review-band links become
    # candidates the user must confirm before they merge.
    confident = all_links[all_links["score"] >= config.thresholds.match]
    candidate = all_links[
        (all_links["score"] >= config.thresholds.review)
        & (all_links["score"] < config.thresholds.match)
    ]

    clusters = cluster.assign_master_ids(n, confident)

    # Keep only candidates that would actually merge two *different* base clusters
    # (drop ones already connected confidently).
    master = dict(zip(clusters["row_idx"], clusters["master_id"], strict=False))
    if not candidate.empty:
        keep = [
            master.get(int(a)) != master.get(int(b))
            for a, b in zip(candidate["idx_a"], candidate["idx_b"], strict=False)
        ]
        candidate = candidate[keep]
    candidates = candidate.reset_index(drop=True)

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
        "possible_duplicates": int(len(candidates)),  # review-band pairs awaiting a call
    }
    progress("Done", 1.0)
    return DedupeResult(
        clusters=clusters, candidates=candidates, norm=norm, counts=counts
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Link-frame helpers — schema padding and pair canonicalization
# (order each pair idx_a < idx_b, keep the best-scoring copy of duplicates)
# ══════════════════════════════════════════════════════════════════════════


def _empty_links() -> pd.DataFrame:
    return pd.DataFrame(columns=_LINK_COLS)


def _with_link_cols(links: pd.DataFrame) -> pd.DataFrame:
    """Ensure every stage's output carries the full link schema (adds adjustments="")."""
    out = links.copy()
    if "adjustments" not in out.columns:
        out["adjustments"] = ""
    return out[_LINK_COLS]


def _normalize_pairs(links: pd.DataFrame) -> pd.DataFrame:
    """Order each pair as idx_a < idx_b and keep the single best-scoring copy."""
    if links.empty:
        return _empty_links()
    a = links["idx_a"].astype(int)
    b = links["idx_b"].astype(int)
    out = links.copy()
    out["idx_a"] = a.where(a < b, b)
    out["idx_b"] = b.where(a < b, a)
    out = out[out["idx_a"] != out["idx_b"]]
    return (
        out.sort_values("score", ascending=False)
        .drop_duplicates(subset=["idx_a", "idx_b"], keep="first")
        .reset_index(drop=True)
    )
