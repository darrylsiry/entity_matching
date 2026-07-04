# syntax=docker/dockerfile:1

# ── Stage 1: build the React/Vite frontend into static assets ────────────────
FROM node:22-alpine AS frontend
WORKDIR /frontend
# Install deps first (cached unless the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build                      # -> /frontend/dist

# ── Stage 2: Python backend runtime, also serving the built frontend ─────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ENTITY_MATCHING_DATA_DIR=/data \
    FRONTEND_DIST=/app/static \
    PORT=8080

WORKDIR /app

# All backend deps ship as manylinux wheels for cp311, so no compiler is needed.
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /frontend/dist ./static

# Writable data dir for uploads/results (mount a Fly volume here to persist).
RUN mkdir -p /data

EXPOSE 8080

# Bind to 0.0.0.0 so Fly's proxy can reach it. Respect $PORT if the platform sets one.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
