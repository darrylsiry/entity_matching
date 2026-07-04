.PHONY: dev install sample test backend frontend

# Start backend + frontend together (Ctrl+C stops both).
dev:
	./dev.sh

# One-time setup: create the backend venv and install all deps.
install:
	cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

# Regenerate the demo CSV files.
sample:
	backend/.venv/bin/python sample_data/generate.py

# Run backend unit tests.
test:
	cd backend && .venv/bin/pytest -q

# Run just one side (handy for debugging).
backend:
	cd backend && .venv/bin/uvicorn app.main:app --port 8000 --reload

frontend:
	cd frontend && npm run dev
