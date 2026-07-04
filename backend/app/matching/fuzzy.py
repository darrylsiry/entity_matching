"""Stage 3 — fuzzy fallback.

For records not resolved deterministically and (optionally) not covered by the
probabilistic stage, do a blocked fuzzy name comparison with rapidfuzz.

Blocking: each record contributes one key per name token — the token's first
`_PREFIX_LEN` characters. Two records are compared if they share **any** block key.
This keeps comparisons tractable (no A x B) while tolerating both word-order
differences ("Acme Global" / "Global Acme") and typos after the prefix
("Umbrella" / "Umbrela" both -> "umbr"). Within a candidate set we score name
similarity with token_set_ratio, boosted when city/postcode also agree.
"""

from __future__ import annotations

import pandas as pd
from rapidfuzz import fuzz

from .schema import CanonicalField, norm_col

_NAME = norm_col(CanonicalField.NAME)
_ADDRESS = norm_col(CanonicalField.ADDRESS)
_CITY = norm_col(CanonicalField.CITY)
_POSTCODE = norm_col(CanonicalField.POSTCODE)

_PREFIX_LEN = 4  # chars of each token used as a blocking key

# Corroboration weights. A name match is inherently uncertain; agreeing secondary
# fields raise confidence and a *conflicting address* lowers it. The address-conflict
# penalty is deliberately large enough to push a name-only coincidence
# ("Pacific Leasing" vs "Pacific Cleaning" at different addresses) out of the auto-match
# band and into review, rather than silently accepting it.
_ADDR_AGREE = 0.05
_ADDR_CONFLICT = -0.18
_FIELD_AGREE = 0.03


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


def _block_keys(name: str) -> list[str]:
    """One key per token: its first _PREFIX_LEN chars."""
    return [tok[:_PREFIX_LEN] for tok in name.split() if tok]


def corroborate(name_score: float, row_a, row_b, has: dict) -> float:
    """Adjust a name-similarity score using secondary fields.

    Agreement (matching city/postcode, similar address) nudges it up; a clearly
    different address pulls it down so a coincidental name match is surfaced for
    review instead of auto-accepted. Missing fields never penalize. Clamped to [0, 1].
    """
    delta = 0.0
    if has.get("addr"):
        av, bv = row_a[_ADDRESS], row_b[_ADDRESS]
        if av and bv:
            s = fuzz.token_set_ratio(av, bv) / 100.0
            if s >= 0.70:
                delta += _ADDR_AGREE
            elif s < 0.45:
                delta += _ADDR_CONFLICT
    if has.get("city") and row_a[_CITY] and row_a[_CITY] == row_b[_CITY]:
        delta += _FIELD_AGREE
    if has.get("pc") and row_a[_POSTCODE] and row_a[_POSTCODE] == row_b[_POSTCODE]:
        delta += _FIELD_AGREE
    return max(0.0, min(1.0, name_score + delta))


def _availability(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    return {
        "addr": _ADDRESS in a and _ADDRESS in b,
        "city": _CITY in a and _CITY in b,
        "pc": _POSTCODE in a and _POSTCODE in b,
    }


def name_similarity(a: str, b: str) -> float:
    """Length-aware name similarity in [0, 1].

    Uses min(token_sort_ratio, token_set_ratio) rather than token_set_ratio alone.
    token_set_ratio returns 1.0 whenever one name's tokens are a *subset* of the
    other's ("Apple" vs "Apple Bank for Savings" -> 1.0), which is a major source of
    false positives. token_sort_ratio has no such subset leniency, so taking the min
    keeps genuine matches (and typos) high while collapsing subset coincidences.
    """
    return min(fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b)) / 100.0


def match_fuzzy(
    a: pd.DataFrame,
    b: pd.DataFrame,
    threshold: float = 0.80,
    exclude_a: set[int] | None = None,
    exclude_b: set[int] | None = None,
) -> pd.DataFrame:
    """Blocked fuzzy name matching.

    `threshold` is the minimum name similarity (0-1) to emit a pair. `exclude_a` /
    `exclude_b` are positional indices already resolved by earlier stages and skipped.
    For each A record only its single best B match above threshold is kept.
    """
    exclude_a = exclude_a or set()
    exclude_b = exclude_b or set()

    a_use = a.loc[(a[_NAME] != "") & (~a.index.isin(exclude_a))]
    b_use = b.loc[(b[_NAME] != "") & (~b.index.isin(exclude_b))]
    if a_use.empty or b_use.empty:
        return _empty_pairs()

    # Inverted index: block key -> B indices. A record is compared against the union
    # of B records sharing any of its keys.
    b_blocks: dict[str, list[int]] = {}
    for idx, name in b_use[_NAME].items():
        for key in _block_keys(name):
            b_blocks.setdefault(key, []).append(idx)

    has = _availability(a_use, b_use)

    rows: list[dict] = []
    for idx_a, row_a in a_use.iterrows():
        name_a = row_a[_NAME]
        candidates = {
            idx_b for key in _block_keys(name_a) for idx_b in b_blocks.get(key, ())
        }
        best_idx, best_score = None, 0.0
        for idx_b in candidates:
            row_b = b_use.loc[idx_b]
            score = corroborate(name_similarity(name_a, row_b[_NAME]), row_a, row_b, has)
            if score > best_score:
                best_idx, best_score = idx_b, score
        if best_idx is not None and best_score >= threshold:
            rows.append(
                {
                    "idx_a": idx_a,
                    "idx_b": best_idx,
                    "score": round(best_score, 4),
                    "stage": "fuzzy",
                    "rule": "name_token_set",
                }
            )

    if not rows:
        return _empty_pairs()
    return pd.DataFrame(rows)


def match_fuzzy_within(
    a: pd.DataFrame,
    threshold: float = 0.80,
    exclude: set[int] | None = None,
) -> pd.DataFrame:
    """Blocked fuzzy name matching among rows of a single frame (for dedupe).

    Emits every i<j pair whose name similarity is >= threshold (not just best-per-row),
    so multi-row duplicate clusters are fully linked. `exclude` skips rows already
    resolved deterministically.
    """
    exclude = exclude or set()
    use = a.loc[(a[_NAME] != "") & (~a.index.isin(exclude))]
    if len(use) < 2:
        return _empty_pairs()

    blocks: dict[str, list[int]] = {}
    for idx, name in use[_NAME].items():
        for key in _block_keys(name):
            blocks.setdefault(key, []).append(idx)

    has = _availability(use, use)

    seen: set[tuple[int, int]] = set()
    rows: list[dict] = []
    for idx_i, row_i in use.iterrows():
        name_i = row_i[_NAME]
        candidates = {
            j for key in _block_keys(name_i) for j in blocks.get(key, ()) if j != idx_i
        }
        for idx_j in candidates:
            lo, hi = (idx_i, idx_j) if idx_i < idx_j else (idx_j, idx_i)
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            row_j = use.loc[idx_j]
            score = corroborate(name_similarity(name_i, row_j[_NAME]), row_i, row_j, has)
            if score >= threshold:
                rows.append(
                    {"idx_a": lo, "idx_b": hi, "score": round(score, 4),
                     "stage": "fuzzy", "rule": "name_token_set"}
                )

    if not rows:
        return _empty_pairs()
    return pd.DataFrame(rows)
