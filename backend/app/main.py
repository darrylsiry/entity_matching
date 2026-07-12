"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import storage
from .api.routes import router

logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════════════════════════════════
# SECTION: App assembly — create the FastAPI app, allow the Vite dev server
# via CORS, mount the /api routes, and init the on-disk storage dirs.
# ══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Entity Matching", version="0.1.0")

# Dev CORS: allow the Vite dev server. In production the built frontend is served
# from this same origin (see the static mount below), so CORS isn't involved.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    storage.init_storage()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Production static serving — one process serves API + React app
# ══════════════════════════════════════════════════════════════════════════

# Serve the compiled frontend (if present) from the same origin. FRONTEND_DIST points
# at the Vite build output; the Docker image copies it in. Mounted LAST so it only
# catches paths the API/health routes didn't — Starlette matches routes in order.
_frontend_dist = os.environ.get("FRONTEND_DIST")
if _frontend_dist and Path(_frontend_dist).is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
