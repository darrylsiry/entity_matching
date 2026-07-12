"""Dump the FastAPI OpenAPI schema to JSON (no server needed).

Used by `npm run gen:api` in frontend/ to generate TypeScript types, keeping the
frontend's API types in lockstep with the backend's Pydantic models. CI regenerates
and fails on drift.

Usage: python scripts/export_openapi.py [output_path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
