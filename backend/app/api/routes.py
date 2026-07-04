"""HTTP API for the entity matching app."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from .. import evaluation, review, service, storage
from ..jobs import JobState, registry
from ..matching.schema import FIELD_SPECS
from ..matching.scoring import MATCH, REVIEW
from ..models import (
    ClusterOut,
    ClustersResponse,
    ColumnInfo,
    CreateSessionRequest,
    CreateSessionResponse,
    DecisionRequest,
    DecisionResponse,
    EvaluationResponse,
    FieldSpecOut,
    MatchPair,
    ResultsResponse,
    StatusResponse,
    UploadResponse,
)

router = APIRouter(prefix="/api")

_SAMPLE_ROWS = 5


@router.get("/fields", response_model=list[FieldSpecOut])
def get_fields() -> list[FieldSpecOut]:
    """Canonical fields the user maps their columns onto."""
    return [
        FieldSpecOut(field=s.field, label=s.label, required=s.required, help=s.help)
        for s in FIELD_SPECS
    ]


def _read_table(raw: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw), dtype=str)
    # default: CSV (utf-8, fall back to latin-1 for messy exports)
    try:
        return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(
            io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="latin-1"
        )


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    try:
        df = _read_table(raw, file.filename or "upload.csv")
    except Exception as exc:
        raise HTTPException(400, f"could not parse file: {exc}") from exc
    if df.empty or len(df.columns) == 0:
        raise HTTPException(400, "file has no columns/rows")

    file_id, columns = storage.save_upload(df, file.filename or "upload.csv")

    sample = df.head(_SAMPLE_ROWS).fillna("").astype(str)
    col_info = [
        ColumnInfo(name=c, sample=sample[c].tolist()) for c in columns
    ]
    return UploadResponse(
        file_id=file_id, filename=file.filename or "upload.csv",
        rows=int(len(df)), columns=col_info,
    )


@router.post("/session", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    # Which (file, mapping) pairs to validate depends on the mode.
    to_check = [(req.file_id_a, req.mapping_a)]
    if req.mode == "link":
        if not req.file_id_b:
            raise HTTPException(400, "link mode requires a second file (file_id_b)")
        to_check.append((req.file_id_b, req.mapping_b))

    for fid, mapping in to_check:
        try:
            meta = storage.upload_meta(fid)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        cols = set(meta["columns"])
        for field, src in mapping.items():
            if src not in cols:
                raise HTTPException(
                    400, f"mapping for '{field}' points to unknown column '{src}'"
                )

    session_id = storage.new_id("sess")
    storage.save_session_meta(session_id, {"request": req.model_dump(mode="json")})
    return CreateSessionResponse(session_id=session_id)


@router.post("/session/{session_id}/run", response_model=StatusResponse)
def run_session(session_id: str, background: BackgroundTasks) -> StatusResponse:
    try:
        storage.load_session_meta(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    existing = registry.get(session_id)
    if existing and existing.state == JobState.RUNNING:
        raise HTTPException(409, "job already running")

    registry.create(session_id)
    background.add_task(service.run_match_job, session_id)
    return _status(session_id)


@router.get("/session/{session_id}/status", response_model=StatusResponse)
def get_status(session_id: str) -> StatusResponse:
    return _status(session_id)


def _status(session_id: str) -> StatusResponse:
    job = registry.get(session_id)
    if job is None:
        # No live job — infer from persisted results.
        if storage.result_exists(session_id):
            return StatusResponse(
                session_id=session_id, state="done", stage="Done", progress=1.0
            )
        raise HTTPException(404, "no job for session")
    return StatusResponse(
        session_id=session_id, state=job.state.value, stage=job.stage,
        progress=job.progress, error=job.error, counts=job.counts,
    )


@router.get("/session/{session_id}/results", response_model=ResultsResponse)
def get_results(
    session_id: str,
    match_class: str | None = Query(
        None, description="filter: pairs|match|review|dismissed|unique_a|unique_b"
    ),
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=500),
) -> ResultsResponse:
    try:
        eff = review.effective_frame(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc

    # "dismissed" is an audit view sourced from the base frame, not the effective one.
    if match_class == "dismissed":
        pairs = review.dismissed_frame(session_id)
    elif match_class == "pairs":
        pairs = eff[eff["match_class"].isin([MATCH, REVIEW])]
    elif match_class:
        pairs = eff[eff["match_class"] == match_class]
    else:
        pairs = eff

    counts = review.effective_counts(session_id)
    total = int(len(pairs))
    start = page * page_size
    page_pairs = pairs.iloc[start : start + page_size]
    enriched = service.enrich_pairs(session_id, page_pairs)

    return ResultsResponse(
        session_id=session_id, total=total, page=page, page_size=page_size,
        counts=counts, pairs=[MatchPair(**p) for p in enriched],
    )


@router.post("/session/{session_id}/decision", response_model=DecisionResponse)
def decide(session_id: str, req: DecisionRequest) -> DecisionResponse:
    """Confirm, dismiss, or clear a decision on a review pair."""
    try:
        review.set_decision(session_id, req.idx_a, req.idx_b, req.decision)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DecisionResponse(
        session_id=session_id, counts=review.effective_counts(session_id)
    )


@router.get("/session/{session_id}/evaluation", response_model=EvaluationResponse)
def get_evaluation(session_id: str) -> EvaluationResponse:
    """Debug: score the session's matches against the repo's ground-truth file."""
    try:
        result = evaluation.evaluate(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return EvaluationResponse(**result)


def _session_mode(session_id: str) -> str:
    try:
        meta = storage.load_session_meta(session_id)
    except FileNotFoundError:
        return "link"
    return meta.get("request", {}).get("mode", "link")


@router.get("/session/{session_id}/clusters", response_model=ClustersResponse)
def get_clusters(
    session_id: str,
    filter: str = Query("duplicates", description="duplicates|all|unique"),
    page: int = Query(0, ge=0),
    page_size: int = Query(25, ge=1, le=200),
) -> ClustersResponse:
    """Dedupe results: rows grouped into master-ID clusters."""
    try:
        rows = storage.load_result(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc
    if "master_id" not in rows.columns:
        raise HTTPException(400, "this session is not a dedupe session")

    if filter == "duplicates":
        rows = rows[rows["cluster_size"] > 1]
    elif filter == "unique":
        rows = rows[rows["cluster_size"] == 1]

    # Order clusters largest-first so duplicates surface at the top.
    master_order = (
        rows.drop_duplicates("master_id")
        .sort_values(["cluster_size", "master_id"], ascending=[False, True])["master_id"]
        .tolist()
    )
    total = len(master_order)
    page_ids = master_order[page * page_size : page * page_size + page_size]
    page_rows = rows[rows["master_id"].isin(page_ids)]
    # Preserve the largest-first ordering within the page.
    page_rows = page_rows.assign(
        _o=page_rows["master_id"].map({m: i for i, m in enumerate(page_ids)})
    ).sort_values(["_o", "row_idx"])

    clusters = service.build_clusters(session_id, page_rows)
    return ClustersResponse(
        session_id=session_id, total=total, page=page, page_size=page_size,
        counts=_dedupe_counts(storage.load_result(session_id)),
        clusters=[ClusterOut(**c) for c in clusters],
    )


def _dedupe_counts(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {"records": 0, "clusters": 0, "duplicate_rows": 0,
                "duplicate_clusters": 0, "unique_rows": 0}
    n_clusters = int(rows["master_id"].nunique())
    dup_clusters = int(rows.loc[rows["cluster_size"] > 1, "master_id"].nunique())
    return {
        "records": int(len(rows)),
        "clusters": n_clusters,
        "duplicate_rows": int((rows["cluster_size"] > 1).sum()),
        "duplicate_clusters": dup_clusters,
        "unique_rows": n_clusters - dup_clusters,
    }


@router.get("/session/{session_id}/download")
def download(session_id: str) -> StreamingResponse:
    if not storage.result_exists(session_id):
        raise HTTPException(404, "no results yet")

    if _session_mode(session_id) == "dedupe":
        out = service.dedupe_download_frame(session_id, storage.load_result(session_id))
        filename = f"deduplicated_{session_id}.csv"
    else:
        # Effective frame -> confirmed reviews export as matches, dismissed as unique.
        rows = []
        for p in service.enrich_pairs(session_id, review.effective_frame(session_id)):
            row = {
                "score": p["score"], "match_class": p["match_class"],
                "decision": p["decision"] or "", "stage": p["stage"], "rule": p["rule"],
            }
            row.update({f"a_{k}": v for k, v in p["record_a"].items()})
            row.update({f"b_{k}": v for k, v in p["record_b"].items()})
            rows.append(row)
        out = pd.DataFrame(rows)
        filename = f"matches_{session_id}.csv"

    buf = io.StringIO()
    out.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
