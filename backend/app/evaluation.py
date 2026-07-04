"""Debug-only accuracy evaluation against a ground-truth match file.

Compares the matches a session produced (auto matches + user-confirmed reviews) against
`ground_truth_matches.csv` at the repo root and reports precision / recall / F1. This is
purely a developer aid — it's a no-op (available=False) when the ground-truth file isn't
present (e.g. in the deployed container) or when the uploaded data doesn't correspond to
that ground truth.

Ground truth format: rows of (file_a_name, file_b_name, ...) naming the correct A↔B
pairs. We key comparisons on the (A name, B name) strings, which come from the same
source files as the ground truth, so a light normalization makes them line up.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

from . import review, storage
from .matching.schema import CanonicalField
from .matching.scoring import MATCH
from .models import CreateSessionRequest

# backend/app/evaluation.py -> parents[2] == repo root
_DEFAULT_GT = Path(__file__).resolve().parents[2] / "ground_truth_matches.csv"
GROUND_TRUTH_PATH = Path(os.environ.get("GROUND_TRUTH_PATH", _DEFAULT_GT))


def _norm(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def evaluate(session_id: str) -> dict:
    """Return precision/recall/F1 of the session's matches vs ground truth."""
    if not GROUND_TRUTH_PATH.exists():
        return _unavailable("ground_truth_matches.csv not found")

    meta = storage.load_session_meta(session_id)
    req = CreateSessionRequest(**meta["request"])
    if req.mode != "link":
        return _unavailable("only two-file matching can be evaluated")

    name_col_a = req.mapping_a.get(CanonicalField.NAME)
    name_col_b = req.mapping_b.get(CanonicalField.NAME)
    if not name_col_a or not name_col_b or not req.file_id_b:
        return _unavailable("company name not mapped")

    gt = pd.read_csv(GROUND_TRUTH_PATH, dtype=str, keep_default_na=False)
    if "file_a_name" not in gt.columns or "file_b_name" not in gt.columns:
        return _unavailable("unexpected ground-truth columns")
    gt_pairs = {(_norm(a), _norm(b)) for a, b in zip(gt["file_a_name"], gt["file_b_name"])}
    gt_a_names = {a for a, _ in gt_pairs}

    raw_a = storage.load_upload(req.file_id_a)
    raw_b = storage.load_upload(req.file_id_b)

    # Does the uploaded file A even correspond to this ground truth?
    file_a_names = {_norm(v) for v in raw_a[name_col_a]}
    applicable = len(file_a_names & gt_a_names) > 0

    eff = review.effective_frame(session_id)
    matches = eff[eff["match_class"] == MATCH]
    predicted: set[tuple[str, str]] = set()
    for r in matches.itertuples(index=False):
        ia, ib = int(r.idx_a), int(r.idx_b)
        if 0 <= ia < len(raw_a) and 0 <= ib < len(raw_b):
            predicted.add((_norm(raw_a.iloc[ia][name_col_a]), _norm(raw_b.iloc[ib][name_col_b])))

    tp = len(predicted & gt_pairs)
    fp = len(predicted - gt_pairs)
    fn = len(gt_pairs - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "available": True,
        "applicable": applicable,
        "reason": None if applicable else "uploaded data doesn't match this ground truth",
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "ground_truth_total": len(gt_pairs),
        "predicted_total": len(predicted),
    }
