"""On-disk persistence for uploads and match results.

Everything lives under a single data directory (default `backend/data/`, overridable
via ENTITY_MATCHING_DATA_DIR). Uploaded tables and result tables are stored as parquet
so 100k-row files load fast and cheaply. This is intentionally simple filesystem
storage — swap for object storage / a DB later without touching callers.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pandas as pd

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = Path(os.environ.get("ENTITY_MATCHING_DATA_DIR", _DEFAULT_DIR))
UPLOAD_DIR = DATA_DIR / "uploads"
SESSION_DIR = DATA_DIR / "sessions"


def init_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---- uploads ---------------------------------------------------------------

def save_upload(df: pd.DataFrame, filename: str) -> tuple[str, list[str]]:
    """Persist an uploaded dataframe; return (file_id, column names)."""
    file_id = new_id("file")
    # All columns to string keeps parquet + downstream normalization predictable.
    df = df.astype("string")
    df.to_parquet(UPLOAD_DIR / f"{file_id}.parquet", index=False)
    meta = {"file_id": file_id, "filename": filename, "columns": list(df.columns),
            "rows": int(len(df))}
    (UPLOAD_DIR / f"{file_id}.json").write_text(json.dumps(meta))
    return file_id, list(df.columns)


def load_upload(file_id: str) -> pd.DataFrame:
    path = UPLOAD_DIR / f"{file_id}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"unknown file_id: {file_id}")
    return pd.read_parquet(path)


def upload_meta(file_id: str) -> dict:
    path = UPLOAD_DIR / f"{file_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown file_id: {file_id}")
    return json.loads(path.read_text())


# ---- session config --------------------------------------------------------

def save_session_meta(session_id: str, meta: dict) -> None:
    (SESSION_DIR / f"{session_id}.json").write_text(json.dumps(meta))


def load_session_meta(session_id: str) -> dict:
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown session: {session_id}")
    return json.loads(path.read_text())


# ---- session results -------------------------------------------------------

def save_result(session_id: str, results: pd.DataFrame) -> None:
    results.to_parquet(SESSION_DIR / f"{session_id}_results.parquet", index=False)


def load_result(session_id: str) -> pd.DataFrame:
    path = SESSION_DIR / f"{session_id}_results.parquet"
    if not path.exists():
        raise FileNotFoundError(f"no results for session: {session_id}")
    return pd.read_parquet(path)


def result_exists(session_id: str) -> bool:
    return (SESSION_DIR / f"{session_id}_results.parquet").exists()


# ---- review decisions (small overlay, kept out of the big result parquet) ---

def load_decisions(session_id: str) -> dict:
    path = SESSION_DIR / f"{session_id}_decisions.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_decisions(session_id: str, decisions: dict) -> None:
    (SESSION_DIR / f"{session_id}_decisions.json").write_text(json.dumps(decisions))
