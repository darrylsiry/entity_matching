"""Stage 1 — deterministic matching.

Exact joins on the strongest identifiers, in priority order:
  1. registration / tax id   (guarded by id *type* when both sides declare one)
  2. website domain          (corroboration-guarded, see below)
  3. phone number            (corroboration-guarded, see below)

Registration-id agreement is near-certain and always scores 0.99. Shared domains and
phones are strong but not conclusive — subsidiaries, franchises, and portfolio
companies share websites and switchboards. When corroboration is enabled, a
domain/phone-only pair whose *names* look nothing alike (fuzzy name similarity below
`name_floor`) is demoted into the review band instead of auto-matching, and its
records stay in the pool for later stages.

A "pair" throughout the pipeline is a row with (idx_a, idx_b, score, stage, rule).
idx_a / idx_b are positional row indices into the two normalized frames.
"""

from __future__ import annotations

import pandas as pd

from .schema import NORM_DOMAIN, CanonicalField, norm_col

# Score assigned to a confident deterministic hit. Just under 1.0 so a later
# corroborating signal could in principle nudge it, but comfortably above any match
# threshold.
DETERMINISTIC_SCORE = 0.99

_REG_ID = norm_col(CanonicalField.REGISTRATION_ID)
_PHONE = norm_col(CanonicalField.PHONE)
_NAME = norm_col(CanonicalField.NAME)

# (canonical column, rule label) in priority order. Domain/phone are subject to the
# name-corroboration guard; registration_id never is.
_GUARDED_KEYS = [(NORM_DOMAIN, "domain"), (_PHONE, "phone")]


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


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


def _id_types_compatible(id_type_a: str, id_type_b: str) -> bool:
    """Ids only join when types agree or at least one side is unknown (spec 1.7)."""
    return id_type_a == "unknown" or id_type_b == "unknown" or id_type_a == id_type_b


def _apply_corroboration(
    pairs: pd.DataFrame,
    a: pd.DataFrame,
    b: pd.DataFrame,
    regid_set: set[tuple[int, int]],
    name_floor: float,
    demoted_score: float,
) -> pd.DataFrame:
    """Demote domain/phone pairs whose names conflict (and no reg-id backs them up)."""
    from .fuzzy import name_similarity  # local import avoids a cycle at module load

    scores: list[float] = []
    rules: list[str] = []
    for r in pairs.itertuples(index=False):
        ia, ib = int(r.idx_a), int(r.idx_b)
        if (ia, ib) in regid_set:  # reg-id agreement overrides the guard
            scores.append(DETERMINISTIC_SCORE)
            rules.append(r.rule)
            continue
        sim = name_similarity(str(a.at[ia, _NAME]), str(b.at[ib, _NAME]))
        if sim >= name_floor:
            scores.append(DETERMINISTIC_SCORE)
            rules.append(f"{r.rule}+name_ok")
        else:
            scores.append(demoted_score)
            rules.append(f"{r.rule}_name_conflict")
    out = pairs.copy()
    out["score"] = scores
    out["rule"] = rules
    return out


def match_deterministic(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    use_phone: bool = True,
    corroboration: bool = True,
    name_floor: float = 0.30,
    demoted_score: float = 0.80,
    id_type_a: str = "unknown",
    id_type_b: str = "unknown",
) -> pd.DataFrame:
    """Return deterministic pairs between normalized frames `a` and `b`.

    A given (idx_a, idx_b) is emitted once even if several keys agree; the highest-
    priority rule wins (registration_id > domain > phone).
    """
    frames: list[pd.DataFrame] = []
    regid_set: set[tuple[int, int]] = set()

    if _REG_ID in a and _REG_ID in b and _id_types_compatible(id_type_a, id_type_b):
        reg = _exact_join(a, b, _REG_ID, "registration_id")
        if not reg.empty:
            frames.append(reg)
            regid_set = set(zip(reg["idx_a"].astype(int), reg["idx_b"].astype(int), strict=False))

    for key, rule in _GUARDED_KEYS:
        if rule == "phone" and not use_phone:
            continue
        if key not in a or key not in b:
            continue
        hit = _exact_join(a, b, key, rule)
        if hit.empty:
            continue
        if corroboration:
            hit = _apply_corroboration(
                hit, a, b, regid_set, name_floor, demoted_score
            )
        frames.append(hit)

    if not frames:
        return _empty_pairs()
    pairs = pd.concat(frames, ignore_index=True)
    # Keep the best copy of each pair: reg-id (0.99, first) beats a demoted duplicate.
    pairs = pairs.sort_values("score", ascending=False, kind="stable")
    return pairs.drop_duplicates(subset=["idx_a", "idx_b"], keep="first").reset_index(
        drop=True
    )


def match_within(
    a: pd.DataFrame,
    *,
    use_phone: bool = True,
    corroboration: bool = True,
    name_floor: float = 0.30,
    demoted_score: float = 0.80,
) -> pd.DataFrame:
    """Deterministic pairs among rows of a single frame (for dedupe).

    Same keys and guards as the two-file variant; only i<j pairs are emitted
    (no self-pairs, no mirror duplicates).
    """
    frames: list[pd.DataFrame] = []
    regid_set: set[tuple[int, int]] = set()

    if _REG_ID in a:
        reg = _exact_join(a, a, _REG_ID, "registration_id")
        reg = reg[reg["idx_a"] < reg["idx_b"]]
        if not reg.empty:
            frames.append(reg)
            regid_set = set(zip(reg["idx_a"].astype(int), reg["idx_b"].astype(int), strict=False))

    for key, rule in _GUARDED_KEYS:
        if rule == "phone" and not use_phone:
            continue
        if key not in a:
            continue
        hit = _exact_join(a, a, key, rule)
        hit = hit[hit["idx_a"] < hit["idx_b"]]
        if hit.empty:
            continue
        if corroboration:
            hit = _apply_corroboration(hit, a, a, regid_set, name_floor, demoted_score)
        frames.append(hit)

    if not frames:
        return _empty_pairs()
    pairs = pd.concat(frames, ignore_index=True)
    pairs = pairs.sort_values("score", ascending=False, kind="stable")
    return pairs.drop_duplicates(subset=["idx_a", "idx_b"], keep="first").reset_index(
        drop=True
    )
