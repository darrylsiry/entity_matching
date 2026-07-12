"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .matching.schema import CanonicalField

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Uploads & field metadata — shapes for POST /upload and GET /fields
# ══════════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Matching configuration — everything the Configure step can set.
# These mirror pipeline.MatchConfig field-for-field (service.py translates).
# ══════════════════════════════════════════════════════════════════════════


class ThresholdConfig(BaseModel):
    match: float = 0.90
    review: float = 0.75


class StageThresholdOverride(BaseModel):
    """Optional per-stage overrides; None inherits the global threshold."""

    match: float | None = None
    review: float | None = None


RegistrationIdType = Literal[
    "unknown", "duns", "ein", "vat", "company_number", "lei", "other"
]


class SessionConfig(BaseModel):
    use_deterministic: bool = True
    use_probabilistic: bool = True
    use_fuzzy: bool = True
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    fuzzy_threshold: float = 0.80
    probabilistic_threshold: float = 0.75
    # --- matching-improvements spec, Phase 1 ---
    use_phone: bool = True              # only takes effect if a phone column is mapped
    default_phone_region: str = "US"    # region hint for numbers without country code
    use_phonetic_blocking: bool = True  # metaphone blocking keys (recall only)
    splink_tf_adjustments: bool = True  # term-frequency adjustments on name/city
    deterministic_corroboration: bool = True   # guard domain/phone 0.99s by name sanity
    deterministic_name_floor: float = 0.30
    deterministic_demoted_score: float = 0.80
    # Per-stage classification overrides, e.g. {"fuzzy": {"match": 0.92}}. Null inherits.
    stage_thresholds: dict[str, StageThresholdOverride] | None = None


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Session lifecycle — create a run (files + mappings + config) and
# poll its status while the background job executes.
# ══════════════════════════════════════════════════════════════════════════


class CreateSessionRequest(BaseModel):
    # "link" compares two files; "dedupe" finds duplicate rows within one file.
    mode: Literal["link", "dedupe"] = "link"
    file_id_a: str
    mapping_a: dict[CanonicalField, str]
    # Only used in "link" mode. In "dedupe" mode file A is the single input.
    file_id_b: str | None = None
    mapping_b: dict[CanonicalField, str] = Field(default_factory=dict)
    # Optional registration-id type per file; ids only join when types are equal or
    # at least one side is "unknown" (spec 1.7).
    registration_id_type_a: RegistrationIdType = "unknown"
    registration_id_type_b: RegistrationIdType = "unknown"
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


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Link-mode results + review decisions
# MatchPair carries full provenance (stage/rule/adjustments) so reviewers see
# WHY a pair scored what it did; decisions confirm/dismiss review pairs.
# ══════════════════════════════════════════════════════════════════════════


class MatchPair(BaseModel):
    idx_a: int
    idx_b: int
    score: float
    stage: str
    rule: str
    # Comma-joined corroboration signals applied to the score, e.g.
    # "same_postcode,addr_agree" — the pair's provenance for reviewers.
    adjustments: str = ""
    match_class: str
    # None = automatic; "confirmed"/"dismissed" = a user decision on a review pair.
    decision: str | None = None
    record_a: dict[str, str]
    record_b: dict[str, str]


class DecisionRequest(BaseModel):
    idx_a: int
    idx_b: int
    # "pending" clears a prior decision (undo).
    decision: Literal["confirmed", "dismissed", "pending"]


class BulkDecisionRequest(BaseModel):
    """Apply one decision to many pairs at once (bulk review)."""

    pairs: list[DecisionRequest] = Field(max_length=5000)


class DecisionResponse(BaseModel):
    session_id: str
    counts: dict


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Re-classification + evaluation — change thresholds without a
# re-run, and score the match set against a ground-truth sample.
# ══════════════════════════════════════════════════════════════════════════


class ReclassifyRequest(BaseModel):
    """New global thresholds; Stage 4 re-runs on stored scores without re-matching."""

    match: float = Field(ge=0.0, le=1.0)
    review: float = Field(ge=0.0, le=1.0)


class ReclassifyResponse(BaseModel):
    session_id: str
    match: int
    review: int
    # Lowest score any stage emitted at run time — thresholds below this need a re-run.
    min_stored_score: float | None = None
    counts: dict


class EvaluationResponse(BaseModel):
    """Accuracy vs a ground-truth file (uploaded per session, or the repo fallback)."""

    available: bool
    applicable: bool = False
    reason: str | None = None
    gt_source: str = "none"       # uploaded | repo | none
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ground_truth_total: int = 0
    predicted_total: int = 0
    # Best-F1 point from sweeping the match threshold over stored pair scores.
    suggested_match_threshold: float | None = None
    suggested_precision: float | None = None
    suggested_recall: float | None = None
    suggested_f1: float | None = None


class ResultsResponse(BaseModel):
    session_id: str
    total: int
    page: int
    page_size: int
    counts: dict
    pairs: list[MatchPair]


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Dedupe-mode results — master-ID clusters and the "possible
# duplicate" candidates awaiting a confirm/dismiss decision.
# ══════════════════════════════════════════════════════════════════════════

class ClusterMember(BaseModel):
    row_idx: int
    record: dict[str, str]


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
    adjustments: str = ""         # corroboration provenance (same as MatchPair)
    decision: str | None = None   # None = pending; "confirmed" merges; "dismissed" keeps apart
    record_a: dict[str, str]
    record_b: dict[str, str]


class CandidatesResponse(BaseModel):
    session_id: str
    total: int
    page: int
    page_size: int
    counts: dict
    candidates: list[DedupeCandidate]
