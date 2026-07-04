"""FastAPI application entrypoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import storage
from .api.routes import router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Entity Matching", version="0.1.0")

# Dev CORS: allow the Vite dev server. Tighten for production.
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
