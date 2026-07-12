"""HTTP API for the entity matching app."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from .. import dedupe_review, evaluation, review, service, storage
from ..jobs import JobState, registry
from ..matching.schema import FIELD_SPECS
from ..matching.scoring import MATCH, REVIEW
from ..models import (
    BulkDecisionRequest,
    CandidatesResponse,
    ClusterOut,
    ClustersResponse,
    ColumnInfo,
    CreateSessionRequest,
    CreateSessionResponse,
    DecisionRequest,
    DecisionResponse,
    DedupeCandidate,
    EvaluationResponse,
    FieldSpecOut,
    MatchPair,
    ReclassifyRequest,
    ReclassifyResponse,
    ResultsResponse,
    StatusResponse,
    UploadResponse,
)

router = APIRouter(prefix="/api")

_SAMPLE_ROWS = 5
MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100 MB
MAX_UPLOAD_ROWS = 500_000


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
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )
    try:
        df = _read_table(raw, file.filename or "upload.csv")
    except Exception as exc:
        raise HTTPException(400, f"could not parse file: {exc}") from exc
    if df.empty or len(df.columns) == 0:
        raise HTTPException(400, "file has no columns/rows")
    if len(df) > MAX_UPLOAD_ROWS:
        raise HTTPException(413, f"too many rows (max {MAX_UPLOAD_ROWS:,})")

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
    """Confirm / dismiss / clear a decision.

    Link mode: on a review pair (accept or reject the A<->B match).
    Dedupe mode: on a possible-duplicate candidate (merge the rows, or keep them apart).
    """
    try:
        if _session_mode(session_id) == "dedupe":
            dedupe_review.set_candidate_decision(
                session_id, req.idx_a, req.idx_b, req.decision
            )
            counts = dedupe_review.counts(session_id)
        else:
            review.set_decision(session_id, req.idx_a, req.idx_b, req.decision)
            counts = review.effective_counts(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DecisionResponse(session_id=session_id, counts=counts)


@router.post("/session/{session_id}/decisions/bulk", response_model=DecisionResponse)
def decide_bulk(session_id: str, req: BulkDecisionRequest) -> DecisionResponse:
    """Apply one decision to many pairs (bulk review). Unknown pairs are skipped."""
    is_dedupe = _session_mode(session_id) == "dedupe"
    applied = 0
    try:
        for d in req.pairs:
            try:
                if is_dedupe:
                    dedupe_review.set_candidate_decision(
                        session_id, d.idx_a, d.idx_b, d.decision
                    )
                else:
                    review.set_decision(session_id, d.idx_a, d.idx_b, d.decision)
                applied += 1
            except KeyError:
                continue  # pair no longer in a decidable state — skip, don't abort
        counts = (
            dedupe_review.counts(session_id)
            if is_dedupe
            else review.effective_counts(session_id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "no results yet") from exc
    counts["bulk_applied"] = applied
    return DecisionResponse(session_id=session_id, counts=counts)


@router.post("/session/{session_id}/reclassify", response_model=ReclassifyResponse)
def reclassify(session_id: str, req: ReclassifyRequest) -> ReclassifyResponse:
    """Change thresholds and re-run classification on stored scores (no re-matching)."""
    if req.review > req.match:
        raise HTTPException(400, "review threshold must be <= match threshold")
    try:
        summary = service.reclassify(session_id, req.match, req.review)
    except FileNotFoundError as exc:
        raise HTTPException(404, "no stored pair pool for this session") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ReclassifyResponse(
        session_id=session_id,
        match=summary["match"],
        review=summary["review"],
        min_stored_score=summary["min_stored_score"],
        counts=review.effective_counts(session_id),
    )


@router.post("/session/{session_id}/ground_truth", response_model=EvaluationResponse)
async def upload_ground_truth(
    session_id: str, file: UploadFile = File(...)
) -> EvaluationResponse:
    """Upload a labeled sample of true matches to score this session against.

    Expected columns: `file_a_name`, `file_b_name` (extra columns are ignored). A CSV
    with exactly two columns is accepted with the columns taken in order.
    """
    raw = await file.read()
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "empty or oversized file")
    try:
        gt = _read_table(raw, file.filename or "ground_truth.csv")
    except Exception as exc:
        raise HTTPException(400, f"could not parse file: {exc}") from exc

    if not {"file_a_name", "file_b_name"}.issubset(gt.columns):
        if len(gt.columns) == 2:  # two unnamed/other columns: take them in order
            gt.columns = ["file_a_name", "file_b_name"]
        else:
            raise HTTPException(
                400, "expected columns file_a_name,file_b_name (or exactly 2 columns)"
            )
    storage.save_ground_truth(session_id, gt[["file_a_name", "file_b_name"]])
    return EvaluationResponse(**evaluation.evaluate(session_id))


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
    """Dedupe results: rows grouped into master-ID clusters (reflects confirmed merges)."""
    if not storage.result_exists(session_id):
        raise HTTPException(404, "no results yet")
    if _session_mode(session_id) != "dedupe":
        raise HTTPException(400, "this session is not a dedupe session")

    rows = dedupe_review.effective_clusters(session_id)
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
        counts=dedupe_review.counts(session_id),
        clusters=[ClusterOut(**c) for c in clusters],
    )


@router.get("/session/{session_id}/candidates", response_model=CandidatesResponse)
def get_candidates(
    session_id: str,
    filter: str = Query("pending", description="pending|confirmed|dismissed|all"),
    page: int = Query(0, ge=0),
    page_size: int = Query(25, ge=1, le=200),
) -> CandidatesResponse:
    """Dedupe "possible duplicates": review-band pairs to confirm (merge) or dismiss."""
    if not storage.result_exists(session_id):
        raise HTTPException(404, "no results yet")
    if _session_mode(session_id) != "dedupe":
        raise HTTPException(400, "this session is not a dedupe session")

    view = dedupe_review.candidate_view(session_id)
    if not view.empty:
        if filter == "pending":
            view = view[view["decision"].isna()]
        elif filter in ("confirmed", "dismissed"):
            view = view[view["decision"] == filter]
        view = view.sort_values("score", ascending=False)

    total = int(len(view))
    page_rows = view.iloc[page * page_size : page * page_size + page_size]
    enriched = service.enrich_candidates(session_id, page_rows)
    return CandidatesResponse(
        session_id=session_id, total=total, page=page, page_size=page_size,
        counts=dedupe_review.counts(session_id),
        candidates=[DedupeCandidate(**c) for c in enriched],
    )


@router.get("/session/{session_id}/download")
def download(session_id: str) -> StreamingResponse:
    if not storage.result_exists(session_id):
        raise HTTPException(404, "no results yet")

    if _session_mode(session_id) == "dedupe":
        # Effective clusters -> confirmed possible-duplicates are merged in the export.
        out = service.dedupe_download_frame(
            session_id, dedupe_review.effective_clusters(session_id)
        )
        filename = f"deduplicated_{session_id}.csv"
    else:
        # Effective frame -> confirmed reviews export as matches, dismissed as unique.
        rows = []
        for p in service.enrich_pairs(session_id, review.effective_frame(session_id)):
            row = {
                "score": p["score"], "match_class": p["match_class"],
                "decision": p["decision"] or "", "stage": p["stage"], "rule": p["rule"],
                "adjustments": p["adjustments"],
            }
            row.update({f"a_{k}": v for k, v in p["record_a"].items()})
            row.update({f"b_{k}": v for k, v in p["record_b"].items()})
            rows.append(row)
        out = pd.DataFrame(rows)
        filename = f"matches_{session_id}.csv"

    buf = io.StringIO()
    _csv_sanitize(out).to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _csv_sanitize(df: pd.DataFrame) -> pd.DataFrame:
    """Neutralize spreadsheet formula injection in exported CSVs.

    A cell like `=cmd|...` or `+HYPERLINK(...)` executes when the CSV is opened in
    Excel/Sheets. Prefix string cells starting with = + - @ with a single quote,
    which spreadsheet apps treat as "display as text".
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(
                lambda v: f"'{v}"
                if isinstance(v, str) and v[:1] in ("=", "+", "-", "@")
                else v
            )
    return out
