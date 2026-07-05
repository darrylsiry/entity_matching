"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .matching.schema import CanonicalField


class ColumnInfo(BaseModel):
    name: str
    sample: list[str]


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    rows: int
    columns: list[ColumnInfo]


class FieldSpecOut(BaseModel):
    field: CanonicalField
    label: str
    required: bool
    help: str


class ThresholdConfig(BaseModel):
    match: float = 0.90
    review: float = 0.75


class SessionConfig(BaseModel):
    use_deterministic: bool = True
    use_probabilistic: bool = True
    use_fuzzy: bool = True
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    fuzzy_threshold: float = 0.80
    probabilistic_threshold: float = 0.75


class CreateSessionRequest(BaseModel):
    # "link" compares two files; "dedupe" finds duplicate rows within one file.
    mode: Literal["link", "dedupe"] = "link"
    file_id_a: str
    mapping_a: dict[CanonicalField, str]
    # Only used in "link" mode. In "dedupe" mode file A is the single input.
    file_id_b: str | None = None
    mapping_b: dict[CanonicalField, str] = Field(default_factory=dict)
    config: SessionConfig = Field(default_factory=SessionConfig)


class CreateSessionResponse(BaseModel):
    session_id: str


class StatusResponse(BaseModel):
    session_id: str
    state: str
    stage: str
    progress: float
    error: str | None = None
    counts: dict = Field(default_factory=dict)


class MatchPair(BaseModel):
    idx_a: int
    idx_b: int
    score: float
    stage: str
    rule: str
    match_class: str
    # None = automatic; "confirmed"/"dismissed" = a user decision on a review pair.
    decision: str | None = None
    record_a: dict
    record_b: dict


class DecisionRequest(BaseModel):
    idx_a: int
    idx_b: int
    # "pending" clears a prior decision (undo).
    decision: Literal["confirmed", "dismissed", "pending"]


class DecisionResponse(BaseModel):
    session_id: str
    counts: dict


class EvaluationResponse(BaseModel):
    """Debug-only accuracy vs a ground-truth file."""

    available: bool
    applicable: bool = False
    reason: str | None = None
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ground_truth_total: int = 0
    predicted_total: int = 0


class ResultsResponse(BaseModel):
    session_id: str
    total: int
    page: int
    page_size: int
    counts: dict
    pairs: list[MatchPair]


# ---- dedupe (single-file) results ------------------------------------------

class ClusterMember(BaseModel):
    row_idx: int
    record: dict


class ClusterOut(BaseModel):
    master_id: int
    size: int
    members: list[ClusterMember]


class ClustersResponse(BaseModel):
    session_id: str
    total: int          # number of clusters in the current filter
    page: int
    page_size: int
    counts: dict
    clusters: list[ClusterOut]


class DedupeCandidate(BaseModel):
    idx_a: int
    idx_b: int
    score: float
    stage: str
    decision: str | None = None   # None = pending; "confirmed" merges; "dismissed" keeps apart
    record_a: dict
    record_b: dict


class CandidatesResponse(BaseModel):
    session_id: str
    total: int
    page: int
    page_size: int
    counts: dict
    candidates: list[DedupeCandidate]
