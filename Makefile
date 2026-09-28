# Shortcuts for macOS/Linux. Everything lives in scripts/run.py, which also works on
# Windows: `py scripts\run.py <command>`.
RUN := python3 scripts/run.py

.PHONY: install build run dev test

install:  ## .venv with the Python engine (+ stem separation) and npm deps
	$(RUN) install

build:  ## Build the UI into frontend/dist
	$(RUN) build

run:  ## Build the UI and start Chopper on http://127.0.0.1:8765
	$(RUN) run

dev:  ## API with auto-reload on :8765 + Vite hot reload on :5173
	$(RUN) dev

test:  ## Backend tests + frontend typecheck
	$(RUN) test
