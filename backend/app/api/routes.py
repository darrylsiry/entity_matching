"""HTTP API for the entity matching app."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from .. import service, storage
from ..jobs import JobState, registry
from ..matching.schema import FIELD_SPECS
from ..matching.scoring import MATCH, REVIEW, UNIQUE_A, UNIQUE_B
from ..models import (
    ColumnInfo,
    CreateSessionRequest,
    CreateSessionResponse,
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
    # Validate both files exist and the mappings reference real columns.
    for fid, mapping in ((req.file_id_a, req.mapping_a), (req.file_id_b, req.mapping_b)):
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
        None, description="filter: pairs|match|review|unique_a|unique_b"
    ),
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=500),
) -> ResultsResponse:
    try:
        full = storage.load_result(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc

    pairs = full
    if match_class == "pairs":
        pairs = full[full["match_class"].isin([MATCH, REVIEW])]
    elif match_class:
        pairs = full[full["match_class"] == match_class]

    counts = _summary_counts(full)
    total = int(len(pairs))
    start = page * page_size
    page_pairs = pairs.iloc[start : start + page_size]
    enriched = service.enrich_pairs(session_id, page_pairs)

    return ResultsResponse(
        session_id=session_id, total=total, page=page, page_size=page_size,
        counts=counts, pairs=[MatchPair(**p) for p in enriched],
    )


def _summary_counts(full: pd.DataFrame) -> dict:
    """Reconciling counts derived from the stored result frame.

    For each file: matched + review + unique == total rows.
    """
    vc = full["match_class"].value_counts().to_dict() if not full.empty else {}
    match = int(vc.get(MATCH, 0))
    review = int(vc.get(REVIEW, 0))
    unique_a = int(vc.get(UNIQUE_A, 0))
    unique_b = int(vc.get(UNIQUE_B, 0))
    pair_rows = full[full["match_class"].isin([MATCH, REVIEW])] if not full.empty else full
    matched_b = int(pair_rows["idx_b"].nunique()) if not pair_rows.empty else 0
    return {
        "records_a": match + review + unique_a,
        "records_b": matched_b + unique_b,
        "match": match,
        "review": review,
        "matched_b": matched_b,
        "unique_a": unique_a,
        "unique_b": unique_b,
        "pairs": match + review,
        "total": int(len(full)),
    }


@router.get("/session/{session_id}/download")
def download(session_id: str) -> StreamingResponse:
    try:
        pairs = storage.load_result(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc

    enriched = service.enrich_pairs(session_id, pairs)
    rows = []
    for p in enriched:
        row = {
            "score": p["score"], "match_class": p["match_class"], "stage": p["stage"],
            "rule": p["rule"],
        }
        row.update({f"a_{k}": v for k, v in p["record_a"].items()})
        row.update({f"b_{k}": v for k, v in p["record_b"].items()})
        rows.append(row)
    out = pd.DataFrame(rows)

    buf = io.StringIO()
    out.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=matches_{session_id}.csv"},
    )
