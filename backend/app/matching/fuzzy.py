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
_CITY = norm_col(CanonicalField.CITY)
_POSTCODE = norm_col(CanonicalField.POSTCODE)

_PREFIX_LEN = 4  # chars of each token used as a blocking key


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_a", "idx_b", "score", "stage", "rule"])


def _block_keys(name: str) -> list[str]:
    """One key per token: its first _PREFIX_LEN chars."""
    return [tok[:_PREFIX_LEN] for tok in name.split() if tok]


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

    has_city = _CITY in a_use and _CITY in b_use
    has_pc = _POSTCODE in a_use and _POSTCODE in b_use

    rows: list[dict] = []
    for idx_a, row_a in a_use.iterrows():
        name_a = row_a[_NAME]
        candidates = {
            idx_b for key in _block_keys(name_a) for idx_b in b_blocks.get(key, ())
        }
        best_idx, best_score = None, 0.0
        for idx_b in candidates:
            row_b = b_use.loc[idx_b]
            score = fuzz.token_set_ratio(name_a, row_b[_NAME]) / 100.0
            # Small corroboration boost when location also agrees.
            if has_city and row_a[_CITY] and row_a[_CITY] == row_b[_CITY]:
                score = min(1.0, score + 0.05)
            if has_pc and row_a[_POSTCODE] and row_a[_POSTCODE] == row_b[_POSTCODE]:
                score = min(1.0, score + 0.05)
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
