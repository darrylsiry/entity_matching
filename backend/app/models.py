"""Pydantic request/response schemas for the API."""

from __future__ import annotations

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
    file_id_a: str
    file_id_b: str
    # Maps a canonical field to a source column name. Shared across both files;
    # if a file uses different headers, map to the union — unmapped is fine.
    mapping_a: dict[CanonicalField, str]
    mapping_b: dict[CanonicalField, str]
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
    record_a: dict
    record_b: dict


class ResultsResponse(BaseModel):
    session_id: str
    total: int
    page: int
    page_size: int
    counts: dict
    pairs: list[MatchPair]
