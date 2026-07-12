"""In-process async job registry for match runs.

A match over 100k+ records can take a while, so runs happen off the request thread
and the frontend polls status. This is a simple in-memory registry keyed by
session_id — fine for a single-process prototype; swap for Redis/Celery to scale out.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Job model — one Job per running/finished match session
# ══════════════════════════════════════════════════════════════════════════


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    session_id: str
    state: JobState = JobState.PENDING
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    counts: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Registry — thread-safe in-memory store, keyed by session_id
# The background job writes progress here; GET /status reads it. Process-local
# by design (single machine); swap for Redis to scale out.
# ══════════════════════════════════════════════════════════════════════════


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str) -> Job:
        with self._lock:
            job = Job(session_id=session_id)
            self._jobs[session_id] = job
            return job

    def get(self, session_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(session_id)

    def update(self, session_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(session_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)


registry = JobRegistry()
