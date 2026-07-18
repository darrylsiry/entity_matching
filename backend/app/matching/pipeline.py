"""The cascade orchestrator — runs the matching stages "in a series".

    raw A, raw B
        -> normalize (stage 0)
        -> deterministic  (stage 1)   strong-id / domain / phone exact
        -> probabilistic  (stage 2)   Splink, on records not yet resolved
        -> fuzzy fallback (stage 3)   rapidfuzz, on records still unresolved
        -> combine + classify (stage 4)

Each stage only sees the records earlier stages didn't confidently resolve, so the
expensive Splink step runs on a shrinking pool. A `progress` callback lets the async
job layer surface stage-by-stage status to the UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from . import deterministic, embedding, fuzzy, probabilistic
from .normalize import normalize_frame
from .schema import CanonicalField
from .scoring import (
    MATCH,
    PAIR_COLS,
    REVIEW,
    UNIQUE_A,
    UNIQUE_B,
    Thresholds,
    combine_and_classify,
)

ProgressCb = Callable[[str, float], None]  # (stage label, progress 0..1)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Configuration + result containers
# MatchConfig mirrors the API's SessionConfig (see app/models.py); MatchResult
# bundles everything a caller needs — classified pairs, the raw pre-
# classification pair pool (kept so thresholds can change without re-running),
# unmatched row indices, and summary counts.
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MatchConfig:
    """Toggles + thresholds chosen in the frontend's Configure step."""

    use_deterministic: bool = True
    use_probabilistic: bool = True
    use_fuzzy: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds)
    fuzzy_threshold: float = 0.80
    probabilistic_threshold: float = 0.75
    # --- matching-improvements spec, Phase 1 ---
    use_phone: bool = True
    default_phone_region: str = "US"
    use_phonetic_blocking: bool = True
    splink_tf_adjustments: bool = True
    deterministic_corroboration: bool = True
    deterministic_name_floor: float = 0.30
    deterministic_demoted_score: float = 0.80
    # stage name -> (match_override, review_override); None inherits the global.
    stage_thresholds: dict[str, tuple[float | None, float | None]] | None = None
    registration_id_type_a: str = "unknown"
    registration_id_type_b: str = "unknown"
    # --- matching-improvements spec, Phase 2.1 ---
    # libpostal address parsing; auto-falls back to a static abbreviation-expansion
    # table when the library is not installed.
    use_libpostal: bool = True
    # --- matching-improvements spec, Phase 2.2 ---
    # Semantic (embedding) stage on post-fuzzy residuals. Off by default; candidates
    # are capped at the review band unless city/postcode corroborates.
    use_embedding: bool = False
    embedding_threshold: float = 0.85
    embedding_model: str = embedding.DEFAULT_MODEL


@dataclass
class MatchResult:
    pairs: pd.DataFrame                 # classified best-per-A match/review pairs
    raw_pairs: pd.DataFrame            # ALL stage-emitted pairs pre-classification
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


# ══════════════════════════════════════════════════════════════════════════
# SECTION: The cascade itself (link mode)
# Stage 0 normalize → Stage 1 deterministic → Stage 2 Splink → Stage 3 fuzzy
# → Stage 4 classify. After each stage, records it CONFIDENTLY resolved
# (score >= the Match threshold) are removed from the pool, so later, fuzzier
# stages only work the residuals. Finally, rows in no pair are reported as
# "unique" so per-file counts reconcile: matched + review + unique == rows.
# ══════════════════════════════════════════════════════════════════════════


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
    norm_a = normalize_frame(
        raw_a, mapping_a,
        phone_region=config.default_phone_region,
        use_libpostal=config.use_libpostal,
    )
    norm_b = normalize_frame(
        raw_b, mapping_b,
        phone_region=config.default_phone_region,
        use_libpostal=config.use_libpostal,
    )

    all_pairs: list[pd.DataFrame] = []
    resolved_a: set[int] = set()  # A indices confidently matched, excluded downstream
    resolved_b: set[int] = set()

    if config.use_deterministic:
        progress("Deterministic matching (IDs, domains, phones)", 0.2)
        det = deterministic.match_deterministic(
            norm_a,
            norm_b,
            use_phone=config.use_phone,
            corroboration=config.deterministic_corroboration,
            name_floor=config.deterministic_name_floor,
            demoted_score=config.deterministic_demoted_score,
            id_type_a=config.registration_id_type_a,
            id_type_b=config.registration_id_type_b,
        )
        if not det.empty:
            all_pairs.append(det)
            # Only confident deterministic hits leave the pool; corroboration-demoted
            # pairs (review band) stay in play for later stages.
            strong = det.loc[det["score"] >= config.thresholds.match]
            resolved_a |= set(strong["idx_a"].tolist())
            resolved_b |= set(strong["idx_b"].tolist())

    if config.use_probabilistic:
        progress("Probabilistic linkage (Splink)", 0.45)
        # Run Splink on records not already deterministically resolved.
        sub_a = norm_a.loc[~norm_a.index.isin(resolved_a)]
        sub_b = norm_b.loc[~norm_b.index.isin(resolved_b)]
        if not sub_a.empty and not sub_b.empty:
            prob = probabilistic.match_probabilistic(
                sub_a,
                sub_b,
                threshold=config.probabilistic_threshold,
                use_phone=config.use_phone,
                phonetic_blocking=config.use_phonetic_blocking,
                tf_adjustments=config.splink_tf_adjustments,
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
            phonetic=config.use_phonetic_blocking,
        )
        if not fz.empty:
            all_pairs.append(fz)

    if config.use_embedding:
        progress("Semantic matching (embeddings)", 0.85)
        # Residuals only: records that appear in NO pair from any earlier stage —
        # this stage can only surface candidates nothing else found.
        seen_a, seen_b = set(resolved_a), set(resolved_b)
        for p in all_pairs:
            seen_a |= set(p["idx_a"].tolist())
            seen_b |= set(p["idx_b"].tolist())
        emb = embedding.match_embedding(
            norm_a,
            norm_b,
            threshold=config.embedding_threshold,
            exclude_a=seen_a,
            exclude_b=seen_b,
            model_name=config.embedding_model,
        )
        if not emb.empty:
            all_pairs.append(emb)

    progress("Scoring and classifying", 0.9)
    combined = (
        pd.concat([_with_pair_cols(p) for p in all_pairs], ignore_index=True)
        if all_pairs
        else pd.DataFrame(columns=PAIR_COLS)
    )
    classified = combine_and_classify(
        combined, config.thresholds, stage_thresholds=config.stage_thresholds
    )

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
        raw_pairs=combined,
        unique_a_idx=unique_a_idx,
        unique_b_idx=unique_b_idx,
        norm_a=norm_a,
        norm_b=norm_b,
        counts=counts,
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Frame helpers — normalize stage outputs to one schema, and build
# the single-sided "unique" rows (sentinel -1 on the empty side) that make the
# stored results reconcile against each file's total row count.
# ══════════════════════════════════════════════════════════════════════════


def _with_pair_cols(pairs: pd.DataFrame) -> pd.DataFrame:
    """Ensure every stage's output carries the full pair schema (adds adjustments="")."""
    out = pairs.copy()
    if "adjustments" not in out.columns:
        out["adjustments"] = ""
    return out[PAIR_COLS]


def _unique_frame(unique_a_idx: list[int], unique_b_idx: list[int]) -> pd.DataFrame:
    """Build single-sided rows for unmatched records (sentinel -1 on the empty side)."""
    rows = [
        {"idx_a": i, "idx_b": -1, "score": 0.0, "stage": "", "rule": "",
         "adjustments": "", "match_class": UNIQUE_A}
        for i in unique_a_idx
    ] + [
        {"idx_a": -1, "idx_b": i, "score": 0.0, "stage": "", "rule": "",
         "adjustments": "", "match_class": UNIQUE_B}
        for i in unique_b_idx
    ]
    cols = [*PAIR_COLS, "match_class"]
    return pd.DataFrame(rows, columns=cols)
