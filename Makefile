PY := .venv/bin/python

.PHONY: install build run dev test

install:  ## Python venv (with stem separation) + frontend deps
	python3 -m venv .venv
	$(PY) -m pip install -e ".[separation,dev]"
	cd frontend && npm install

build:  ## Build the UI into frontend/dist (served by the Python server)
	cd frontend && npm run build

run: build  ## Build the UI and start Chopper on http://127.0.0.1:8765
	$(PY) -m chopper

dev:  ## API on :8765 + Vite dev server with hot reload on :5173
	$(PY) -m uvicorn --factory chopper.app:create_app --port 8765 --reload --reload-dir backend & \
	cd frontend && npm run dev

test:  ## Backend tests + frontend typecheck
	$(PY) -m pytest -q
	cd frontend && npm run typecheck
