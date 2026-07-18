"""Glue between the API layer and the matching pipeline.

Runs a match job (updating the job registry as it goes) and enriches raw pair indices
back into full source records for the results/download endpoints.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from . import storage
from .jobs import JobState, registry
from .matching.dedupe import run_dedupe
from .matching.pipeline import MatchConfig, run_pipeline
from .matching.schema import CanonicalField
from .matching.scoring import Thresholds
from .models import CreateSessionRequest

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Config translation — API request (Pydantic) → engine MatchConfig
# Keeps the matching engine free of any web-layer types.
# ══════════════════════════════════════════════════════════════════════════


def _to_match_config(req: CreateSessionRequest) -> MatchConfig:
    cfg = req.config
    stage_thresholds = None
    if cfg.stage_thresholds:
        stage_thresholds = {
            stage: (o.match, o.review) for stage, o in cfg.stage_thresholds.items()
        }
    return MatchConfig(
        use_deterministic=cfg.use_deterministic,
        use_probabilistic=cfg.use_probabilistic,
        use_fuzzy=cfg.use_fuzzy,
        thresholds=Thresholds(match=cfg.thresholds.match, review=cfg.thresholds.review),
        fuzzy_threshold=cfg.fuzzy_threshold,
        probabilistic_threshold=cfg.probabilistic_threshold,
        use_phone=cfg.use_phone,
        default_phone_region=cfg.default_phone_region,
        use_phonetic_blocking=cfg.use_phonetic_blocking,
        splink_tf_adjustments=cfg.splink_tf_adjustments,
        deterministic_corroboration=cfg.deterministic_corroboration,
        deterministic_name_floor=cfg.deterministic_name_floor,
        deterministic_demoted_score=cfg.deterministic_demoted_score,
        stage_thresholds=stage_thresholds,
        registration_id_type_a=req.registration_id_type_a,
        registration_id_type_b=req.registration_id_type_b,
        use_libpostal=cfg.use_libpostal,
        use_embedding=cfg.use_embedding,
        embedding_threshold=cfg.embedding_threshold,
        embedding_model=cfg.embedding_model,
    )


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Stage timing — piggybacks on the progress callback to measure how
# long each pipeline stage took; surfaced in the UI after a run.
# ══════════════════════════════════════════════════════════════════════════


class _StageTimer:
    """Wraps the progress callback to record how long each pipeline stage took."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.timings: dict[str, float] = {}
        self._current: str | None = None
        self._started = 0.0

    def __call__(self, stage: str, p: float) -> None:
        now = time.monotonic()
        if self._current is not None and stage != self._current:
            self.timings[self._current] = round(
                self.timings.get(self._current, 0.0) + (now - self._started), 2
            )
        if stage != self._current:
            self._current, self._started = stage, now
        registry.update(self.session_id, stage=stage, progress=round(p, 3))

    def finish(self) -> dict[str, float]:
        now = time.monotonic()
        if self._current is not None:
            self.timings[self._current] = round(
                self.timings.get(self._current, 0.0) + (now - self._started), 2
            )
        self.timings.pop("Done", None)  # terminal marker, not a real stage
        return self.timings


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Background job — the entry point BackgroundTasks runs off-request
# Loads inputs, runs the right pipeline for the mode, persists results (plus
# the raw pair pool in link mode), and reports state/counts to the registry.
# ══════════════════════════════════════════════════════════════════════════


def run_match_job(session_id: str) -> None:
    """Background entrypoint: execute the pipeline (link or dedupe) and persist."""
    registry.update(session_id, state=JobState.RUNNING, stage="loading", progress=0.0)
    started = time.monotonic()
    try:
        meta = storage.load_session_meta(session_id)
        req = CreateSessionRequest(**meta["request"])
        progress = _StageTimer(session_id)

        config = _to_match_config(req)
        raw_a = storage.load_upload(req.file_id_a)
        mapping_a = {CanonicalField(k): v for k, v in req.mapping_a.items()}

        if req.mode == "dedupe":
            result = run_dedupe(raw_a, mapping_a, config, progress)
            storage.save_result(session_id, result.clusters)
            storage.save_candidates(session_id, result.candidates)
            counts = result.counts
        else:
            raw_b = storage.load_upload(req.file_id_b)
            mapping_b = {CanonicalField(k): v for k, v in req.mapping_b.items()}
            result = run_pipeline(raw_a, raw_b, mapping_a, mapping_b, config, progress)
            # Store matched pairs + single-sided unique rows so results reconcile.
            storage.save_result(session_id, result.storage_frame())
            # Keep ALL stage-emitted pairs so thresholds can be changed and results
            # re-classified later without re-running the pipeline.
            storage.save_allpairs(session_id, result.raw_pairs)
            counts = result.counts

        counts = dict(counts)
        counts["stage_timings"] = progress.finish()
        counts["elapsed_seconds"] = round(time.monotonic() - started, 2)
        logger.info(
            "match job done session=%s mode=%s elapsed=%.2fs timings=%s",
            session_id, req.mode, counts["elapsed_seconds"], counts["stage_timings"],
        )
        registry.update(
            session_id, state=JobState.DONE, stage="Done", progress=1.0, counts=counts
        )
    except Exception as exc:  # surface any failure to the poller
        logger.exception("match job failed: %s", session_id)
        registry.update(session_id, state=JobState.ERROR, error=str(exc))


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Enrichment (link mode) — stored pairs hold only row INDICES; this
# joins the original uploaded rows back on so the UI/CSV can show full records.
# ══════════════════════════════════════════════════════════════════════════


def enrich_pairs(
    session_id: str, pairs: pd.DataFrame
) -> list[dict]:
    """Attach full source records (raw values) to each pair for display."""
    meta = storage.load_session_meta(session_id)
    req = CreateSessionRequest(**meta["request"])
    raw_a = storage.load_upload(req.file_id_a)
    raw_b = storage.load_upload(req.file_id_b)

    has_decision = "decision" in pairs.columns
    has_adjustments = "adjustments" in pairs.columns
    out = []
    for _, row in pairs.iterrows():
        ia, ib = int(row["idx_a"]), int(row["idx_b"])
        decision = row["decision"] if has_decision else None
        if decision is not None and pd.isna(decision):
            decision = None
        adjustments = str(row["adjustments"]) if has_adjustments else ""
        if adjustments == "nan":
            adjustments = ""
        out.append(
            {
                "idx_a": ia,
                "idx_b": ib,
                "score": float(row["score"]),
                "stage": row["stage"],
                "rule": row["rule"],
                "adjustments": adjustments,
                "match_class": row["match_class"],
                "decision": decision,
                "record_a": _record(raw_a, ia),
                "record_b": _record(raw_b, ib),
            }
        )
    return out


def _record(df: pd.DataFrame, idx: int) -> dict:
    if idx < 0 or idx >= len(df):
        return {}
    row = df.iloc[idx].to_dict()
    return {k: ("" if pd.isna(v) else str(v)) for k, v in row.items()}


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Dedupe helpers — load the single source file, group cluster rows
# into member lists for the UI, build the annotated download frame (original
# file + master_id/cluster_size), and enrich candidate pairs with records.
# ══════════════════════════════════════════════════════════════════════════

def load_dedupe_source(session_id: str) -> pd.DataFrame:
    """The single uploaded file for a dedupe session."""
    meta = storage.load_session_meta(session_id)
    req = CreateSessionRequest(**meta["request"])
    return storage.load_upload(req.file_id_a)


def build_clusters(session_id: str, cluster_rows: pd.DataFrame) -> list[dict]:
    """Turn (row_idx, master_id, cluster_size) rows into member-grouped clusters."""
    raw = load_dedupe_source(session_id)
    out: list[dict] = []
    for master_id, grp in cluster_rows.groupby("master_id", sort=False):
        members = [
            {"row_idx": int(r["row_idx"]), "record": _record(raw, int(r["row_idx"]))}
            for _, r in grp.iterrows()
        ]
        out.append(
            {"master_id": int(master_id), "size": len(members), "members": members}
        )
    return out


def dedupe_download_frame(session_id: str, cluster_rows: pd.DataFrame) -> pd.DataFrame:
    """Original file annotated with master_id + cluster_size, in original row order."""
    raw = load_dedupe_source(session_id).reset_index(drop=True)
    ann = cluster_rows.set_index("row_idx")
    out = raw.copy()
    out.insert(0, "master_id", ann["master_id"].reindex(range(len(raw))).values)
    out.insert(1, "cluster_size", ann["cluster_size"].reindex(range(len(raw))).values)
    return out


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Re-classify without re-running — new thresholds over stored scores
# Rebuilds the results frame from the saved pair pool in milliseconds; the
# expensive matching stages never re-execute.
# ══════════════════════════════════════════════════════════════════════════


def reclassify(session_id: str, match_t: float, review_t: float) -> dict:
    """Re-run Stage 4 (classification only) with new thresholds — no re-matching.

    Uses the stored all-pairs frame, rebuilds the reconciled results frame, persists
    it, and updates the session's saved thresholds. Existing review decisions are kept
    (they only apply to pairs that still classify as review). Raises FileNotFoundError
    when the session has no stored all-pairs (e.g. pre-feature or dedupe sessions).
    """
    from .matching.pipeline import _unique_frame
    from .matching.scoring import MATCH, REVIEW, Thresholds, combine_and_classify

    allpairs = storage.load_allpairs(session_id)
    meta = storage.load_session_meta(session_id)
    req = CreateSessionRequest(**meta["request"])
    if req.mode != "link":
        raise ValueError("re-classification is only available for two-file sessions")

    thresholds = Thresholds(match=match_t, review=review_t)
    stage_over = None
    if req.config.stage_thresholds:
        stage_over = {
            s: (o.match, o.review) for s, o in req.config.stage_thresholds.items()
        }
    classified = combine_and_classify(allpairs, thresholds, stage_thresholds=stage_over)

    raw_a = storage.load_upload(req.file_id_a)
    raw_b = storage.load_upload(req.file_id_b)
    matched_a = set(classified["idx_a"].tolist())
    matched_b = set(classified["idx_b"].tolist())
    unique = _unique_frame(
        [i for i in range(len(raw_a)) if i not in matched_a],
        [i for i in range(len(raw_b)) if i not in matched_b],
    )
    frame = (
        pd.concat([classified, unique], ignore_index=True) if not unique.empty
        else classified
    )
    storage.save_result(session_id, frame)

    # Persist the new thresholds so future reads/exports are consistent.
    meta["request"]["config"]["thresholds"] = {"match": match_t, "review": review_t}
    storage.save_session_meta(session_id, meta)

    return {
        "match": int((classified["match_class"] == MATCH).sum()),
        "review": int((classified["match_class"] == REVIEW).sum()),
        # The stored pool can't produce pairs below the lowest score any stage
        # emitted at run time; tell the caller where the floor is.
        "min_stored_score": float(allpairs["score"].min()) if len(allpairs) else None,
    }


# Stage display order for the histogram — cascade order, unknown stages appended.
_STAGE_ORDER = ["deterministic", "probabilistic", "fuzzy", "embedding"]


def score_histogram(session_id: str, n_bins: int = 25) -> dict:
    """Binned score distribution of the stored pair pool, split by stage.

    Bins cover [0.5, 1.0] (the thresholds' slider domain); scores below 0.5 are
    clamped into the first bin. Raises FileNotFoundError when the session has no
    stored pool (pre-feature or dedupe sessions).
    """
    allpairs = storage.load_allpairs(session_id)
    meta = storage.load_session_meta(session_id)
    cfg_thresholds = meta["request"]["config"]["thresholds"]

    lo, hi = 0.5, 1.0
    width = (hi - lo) / n_bins
    # Shared edge values so adjacent bins can never double-count a score that
    # lands exactly on a boundary (per-bin arithmetic differs by float ULPs).
    edges = [lo + i * width for i in range(n_bins + 1)]
    stages_present = [s for s in _STAGE_ORDER if s in set(allpairs["stage"])]
    stages_present += sorted(set(allpairs["stage"]) - set(_STAGE_ORDER))

    clipped = allpairs["score"].clip(lower=lo)
    bins: list[dict] = []
    for i in range(n_bins):
        # Last bin is inclusive of 1.0 so perfect scores land somewhere.
        in_bin = (clipped >= edges[i]) & (
            (clipped < edges[i + 1]) if i < n_bins - 1 else (clipped <= edges[i + 1])
        )
        sub = allpairs.loc[in_bin]
        bins.append(
            {
                "lo": round(edges[i], 4),
                "hi": round(edges[i + 1], 4),
                "counts": {s: int((sub["stage"] == s).sum()) for s in stages_present},
            }
        )

    return {
        "session_id": session_id,
        "stages": stages_present,
        "bins": bins,
        "total": int(len(allpairs)),
        "thresholds": cfg_thresholds,
    }


def enrich_candidates(session_id: str, cand_rows: pd.DataFrame) -> list[dict]:
    """Attach the two rows' records to each possible-duplicate candidate pair."""
    raw = load_dedupe_source(session_id)
    out: list[dict] = []
    for _, r in cand_rows.iterrows():
        ia, ib = int(r["idx_a"]), int(r["idx_b"])
        decision = r["decision"] if "decision" in cand_rows.columns else None
        if decision is not None and pd.isna(decision):
            decision = None
        adjustments = (
            str(r["adjustments"]) if "adjustments" in cand_rows.columns else ""
        )
        if adjustments == "nan":
            adjustments = ""
        out.append(
            {
                "idx_a": ia,
                "idx_b": ib,
                "score": float(r["score"]),
                "stage": r["stage"],
                "adjustments": adjustments,
                "decision": decision,
                "record_a": _record(raw, ia),
                "record_b": _record(raw, ib),
            }
        )
    return out
