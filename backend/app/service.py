"""Glue between the API layer and the matching pipeline.

Runs a match job (updating the job registry as it goes) and enriches raw pair indices
back into full source records for the results/download endpoints.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import storage
from .jobs import JobState, registry
from .matching.dedupe import run_dedupe
from .matching.pipeline import MatchConfig, run_pipeline
from .matching.schema import CanonicalField
from .matching.scoring import Thresholds
from .models import CreateSessionRequest

logger = logging.getLogger(__name__)


def _to_match_config(cfg) -> MatchConfig:
    return MatchConfig(
        use_deterministic=cfg.use_deterministic,
        use_probabilistic=cfg.use_probabilistic,
        use_fuzzy=cfg.use_fuzzy,
        thresholds=Thresholds(match=cfg.thresholds.match, review=cfg.thresholds.review),
        fuzzy_threshold=cfg.fuzzy_threshold,
        probabilistic_threshold=cfg.probabilistic_threshold,
    )


def run_match_job(session_id: str) -> None:
    """Background entrypoint: execute the pipeline (link or dedupe) and persist."""
    registry.update(session_id, state=JobState.RUNNING, stage="loading", progress=0.0)
    try:
        meta = storage.load_session_meta(session_id)
        req = CreateSessionRequest(**meta["request"])

        def progress(stage: str, p: float) -> None:
            registry.update(session_id, stage=stage, progress=round(p, 3))

        config = _to_match_config(req.config)
        raw_a = storage.load_upload(req.file_id_a)
        mapping_a = {CanonicalField(k): v for k, v in req.mapping_a.items()}

        if req.mode == "dedupe":
            result = run_dedupe(raw_a, mapping_a, config, progress)
            storage.save_result(session_id, result.clusters)
            counts = result.counts
        else:
            raw_b = storage.load_upload(req.file_id_b)
            mapping_b = {CanonicalField(k): v for k, v in req.mapping_b.items()}
            result = run_pipeline(raw_a, raw_b, mapping_a, mapping_b, config, progress)
            # Store matched pairs + single-sided unique rows so results reconcile.
            storage.save_result(session_id, result.storage_frame())
            counts = result.counts

        registry.update(
            session_id, state=JobState.DONE, stage="Done", progress=1.0, counts=counts
        )
    except Exception as exc:  # surface any failure to the poller
        logger.exception("match job failed: %s", session_id)
        registry.update(session_id, state=JobState.ERROR, error=str(exc))


def enrich_pairs(
    session_id: str, pairs: pd.DataFrame
) -> list[dict]:
    """Attach full source records (raw values) to each pair for display."""
    meta = storage.load_session_meta(session_id)
    req = CreateSessionRequest(**meta["request"])
    raw_a = storage.load_upload(req.file_id_a)
    raw_b = storage.load_upload(req.file_id_b)

    has_decision = "decision" in pairs.columns
    out = []
    for _, row in pairs.iterrows():
        ia, ib = int(row["idx_a"]), int(row["idx_b"])
        decision = row["decision"] if has_decision else None
        if decision is not None and pd.isna(decision):
            decision = None
        out.append(
            {
                "idx_a": ia,
                "idx_b": ib,
                "score": float(row["score"]),
                "stage": row["stage"],
                "rule": row["rule"],
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


# ---- dedupe (single-file) helpers ------------------------------------------

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
