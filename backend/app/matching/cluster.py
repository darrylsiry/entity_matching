"""Connected-components clustering for single-file dedupe.

Every stage (deterministic, probabilistic, fuzzy) contributes undirected links between
row indices. Rows that are transitively linked belong to the same entity, so we take
the union of all links and compute connected components — each component becomes one
"master" entity. Rows with no links are their own singleton entity.

Implemented with union-find (disjoint-set) so it stays near-linear at 100k+ rows and
needs no extra dependency.
"""

from __future__ import annotations

import pandas as pd

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Union-find (disjoint set) — near-linear connected components
# union(a, b) merges two rows' groups; find(x) returns a group's root with
# path compression. No dependencies, scales to 100k+ rows easily.
# ══════════════════════════════════════════════════════════════════════════


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Master-ID assignment — links in, one master_id per row out
# Rows transitively connected by any link become one entity; unlinked rows are
# singletons. IDs are numbered by smallest member row so re-runs are stable.
# ══════════════════════════════════════════════════════════════════════════


def assign_master_ids(n_rows: int, links: pd.DataFrame) -> pd.DataFrame:
    """Cluster `n_rows` rows given undirected `links` (columns idx_a, idx_b).

    Returns a DataFrame with one row per input row:
        row_idx, master_id, cluster_size

    master_id is a stable 1-based integer: clusters are numbered by their smallest
    member row index, so the same input always yields the same ids.
    """
    uf = _UnionFind(n_rows)
    if links is not None and not links.empty:
        for a, b in zip(links["idx_a"].astype(int), links["idx_b"].astype(int), strict=False):
            if 0 <= a < n_rows and 0 <= b < n_rows:
                uf.union(a, b)

    # Group rows by component root.
    members: dict[int, list[int]] = {}
    for i in range(n_rows):
        members.setdefault(uf.find(i), []).append(i)

    # Number clusters by their smallest member so ids are deterministic.
    ordered_roots = sorted(members, key=lambda r: min(members[r]))
    row_master = [0] * n_rows
    row_size = [0] * n_rows
    for master_id, root in enumerate(ordered_roots, start=1):
        group = members[root]
        for i in group:
            row_master[i] = master_id
            row_size[i] = len(group)

    return pd.DataFrame(
        {
            "row_idx": range(n_rows),
            "master_id": row_master,
            "cluster_size": row_size,
        }
    )
