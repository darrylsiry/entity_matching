"""Stage 3.5 — semantic candidates from sentence embeddings (optional, off by default).

No string metric catches "IBM" vs "International Business Machines" or "VW" vs
"Volkswagen" — the spellings share almost nothing. A sentence-embedding model maps
both to nearby vectors, so cosine similarity finds them where every earlier stage
scores ~0.

Because embeddings also consider same-industry names "similar" ("Delta Airlines" vs
"United Airlines"), this stage is deliberately conservative:

  * It runs ONLY on records still unresolved after the fuzzy stage (no pair emitted
    by any earlier stage), so it can only add candidates nothing else found.
  * A pair found by embedding similarity alone is CAPPED at 0.89 — just below the
    default 0.90 match threshold — so it can never auto-match; it lands in the
    review queue for a human (or LLM adjudicator) to decide.
  * The cap is lifted only when a hard fact corroborates the pair: exact agreement
    on city or postcode.
  * The same corroboration/conflict adjustments as the fuzzy stage apply first
    (address +-, city/postcode/state/country +0.03), recorded as provenance.

Dependencies (`sentence-transformers`, optionally `hnswlib` for large inputs) are
import-guarded like Splink: missing -> log a warning, return nothing, pipeline
continues.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .fuzzy import _availability, corroborate
from .schema import CanonicalField, norm_col

logger = logging.getLogger(__name__)

_NAME = norm_col(CanonicalField.NAME)
_CITY = norm_col(CanonicalField.CITY)
_COUNTRY = norm_col(CanonicalField.COUNTRY)

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Tuning constants
# ══════════════════════════════════════════════════════════════════════════

# Uncorroborated semantic pairs can never reach the default 0.90 match threshold.
_REVIEW_CAP = 0.89
# Corroboration labels that lift the cap (hard geographic facts).
_UNCAP_LABELS = {"same_city", "same_postcode"}
_TOP_K = 5
# Above this many indexed rows, exact numpy search gives way to hnswlib ANN.
_ANN_CUTOFF = 20_000

DEFAULT_MODEL = "all-MiniLM-L6-v2"


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["idx_a", "idx_b", "score", "stage", "rule", "adjustments"]
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Model loading + encoding — optional-import-guarded, model cached
# ══════════════════════════════════════════════════════════════════════════

_MODELS: dict[str, object] = {}


def _load_model(name: str):
    """A cached SentenceTransformer, or None (with one warning) if unavailable."""
    if name in _MODELS:
        return _MODELS[name]
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning(
            "sentence-transformers unavailable, skipping embedding stage: %s", exc
        )
        return None
    try:
        model = SentenceTransformer(name)
    except Exception as exc:  # bad model name / no weights cached and offline
        logger.warning("could not load embedding model %r: %s", name, exc)
        return None
    _MODELS[name] = model
    return model


def _record_text(row) -> str:
    """Embedding input: "name, city, country" with empty parts skipped."""
    parts = [row[_NAME]]
    for col in (_CITY, _COUNTRY):
        if col in row.index and row[col]:
            parts.append(row[col])
    return ", ".join(p for p in parts if p)


def _encode(model, texts: list[str]) -> np.ndarray:
    """Unit-normalized embeddings, so dot product == cosine similarity."""
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Nearest-neighbor search — exact numpy for typical residual sizes,
# hnswlib ANN above _ANN_CUTOFF rows (falls back to exact if not installed).
# ══════════════════════════════════════════════════════════════════════════


def _top_k_cosine(
    queries: np.ndarray, index: np.ndarray, k: int
) -> list[list[tuple[int, float]]]:
    """Per query row: up to k (index position, cosine) pairs, best first."""
    k = min(k, len(index))
    if k == 0:
        return [[] for _ in range(len(queries))]

    if len(index) > _ANN_CUTOFF:
        try:
            import hnswlib

            dim = index.shape[1]
            ann = hnswlib.Index(space="cosine", dim=dim)
            ann.init_index(
                max_elements=len(index), ef_construction=200, M=16, random_seed=42
            )
            ann.add_items(index, np.arange(len(index)))
            ann.set_ef(max(64, 2 * k))
            labels, distances = ann.knn_query(queries, k=k)
            return [
                [(int(j), 1.0 - float(d)) for j, d in zip(row_l, row_d, strict=False)]
                for row_l, row_d in zip(labels, distances, strict=False)
            ]
        except Exception as exc:  # hnswlib missing or failed -> exact search
            logger.info("hnswlib unavailable, using exact search: %s", exc)

    sims = queries @ index.T  # unit vectors -> cosine
    top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    return [
        sorted(
            ((int(j), float(sims[i, j])) for j in top[i]),
            key=lambda t: -t[1],
        )
        for i in range(len(queries))
    ]


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Scoring — corroborate like the fuzzy stage, then cap
# ══════════════════════════════════════════════════════════════════════════


def _score_pair(cosine: float, row_a, row_b, has: dict) -> tuple[float, str]:
    """Adjusted, capped score + comma-joined provenance for one candidate pair."""
    adjusted, applied = corroborate(cosine, row_a, row_b, has)
    if not _UNCAP_LABELS & set(applied):
        adjusted = min(adjusted, _REVIEW_CAP)
    return round(adjusted, 4), ",".join(applied)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Entry points — mirror fuzzy.py
# match_embedding (two files): best B per A record above the cosine threshold.
# match_embedding_within (one file): every i<j pair above the threshold.
# Both are wrapped defensively: any failure returns an empty frame.
# ══════════════════════════════════════════════════════════════════════════


def match_embedding(
    a: pd.DataFrame,
    b: pd.DataFrame,
    threshold: float = 0.85,
    exclude_a: set[int] | None = None,
    exclude_b: set[int] | None = None,
    model_name: str = DEFAULT_MODEL,
) -> pd.DataFrame:
    """Semantic candidates between residuals of two files.

    `threshold` is the minimum raw cosine similarity to consider a neighbor.
    `exclude_a`/`exclude_b` are indices already involved in any earlier-stage pair.
    For each A record only its single best-scoring B candidate is kept.
    """
    exclude_a = exclude_a or set()
    exclude_b = exclude_b or set()
    a_use = a.loc[(a[_NAME] != "") & (~a.index.isin(exclude_a))]
    b_use = b.loc[(b[_NAME] != "") & (~b.index.isin(exclude_b))]
    if a_use.empty or b_use.empty:
        return _empty_pairs()

    model = _load_model(model_name)
    if model is None:
        return _empty_pairs()

    try:
        vecs_a = _encode(model, [_record_text(r) for _, r in a_use.iterrows()])
        vecs_b = _encode(model, [_record_text(r) for _, r in b_use.iterrows()])
        neighbors = _top_k_cosine(vecs_a, vecs_b, _TOP_K)
    except Exception as exc:
        logger.warning("embedding stage failed, skipping: %s", exc)
        return _empty_pairs()

    has = _availability(a_use, b_use)
    b_index = list(b_use.index)

    rows: list[dict] = []
    for pos_a, (idx_a, row_a) in enumerate(a_use.iterrows()):
        best: dict | None = None
        for pos_b, cosine in neighbors[pos_a]:
            if cosine < threshold:
                continue
            idx_b = b_index[pos_b]
            score, adjustments = _score_pair(cosine, row_a, b_use.loc[idx_b], has)
            if best is None or score > best["score"]:
                best = {
                    "idx_a": idx_a,
                    "idx_b": idx_b,
                    "score": score,
                    "stage": "embedding",
                    "rule": "semantic_cosine",
                    "adjustments": adjustments,
                }
        if best is not None:
            rows.append(best)

    if not rows:
        return _empty_pairs()
    return pd.DataFrame(rows)


def match_embedding_within(
    a: pd.DataFrame,
    threshold: float = 0.85,
    exclude: set[int] | None = None,
    model_name: str = DEFAULT_MODEL,
) -> pd.DataFrame:
    """Semantic candidates among residual rows of a single frame (dedupe mode)."""
    exclude = exclude or set()
    use = a.loc[(a[_NAME] != "") & (~a.index.isin(exclude))]
    if len(use) < 2:
        return _empty_pairs()

    model = _load_model(model_name)
    if model is None:
        return _empty_pairs()

    try:
        vecs = _encode(model, [_record_text(r) for _, r in use.iterrows()])
        # +1 neighbor because each row's nearest neighbor is itself.
        neighbors = _top_k_cosine(vecs, vecs, _TOP_K + 1)
    except Exception as exc:
        logger.warning("embedding stage failed, skipping: %s", exc)
        return _empty_pairs()

    has = _availability(use, use)
    index = list(use.index)

    seen: set[tuple[int, int]] = set()
    rows: list[dict] = []
    for pos_i, (idx_i, row_i) in enumerate(use.iterrows()):
        for pos_j, cosine in neighbors[pos_i]:
            idx_j = index[pos_j]
            if idx_j == idx_i or cosine < threshold:
                continue
            lo, hi = (idx_i, idx_j) if idx_i < idx_j else (idx_j, idx_i)
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            score, adjustments = _score_pair(cosine, row_i, use.loc[idx_j], has)
            rows.append(
                {
                    "idx_a": lo,
                    "idx_b": hi,
                    "score": score,
                    "stage": "embedding",
                    "rule": "semantic_cosine",
                    "adjustments": adjustments,
                }
            )

    if not rows:
        return _empty_pairs()
    return pd.DataFrame(rows)
