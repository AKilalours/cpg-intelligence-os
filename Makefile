# One entry point for every task. Every Python command runs with the project's
# own interpreter (.venv), never whatever `python` your shell happens to have.

PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
DATASET ?= m5

.DEFAULT_GOAL := help
.PHONY: help setup lock doctor db migrate stop download load-m5 test api web

help:  ## List commands
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

$(PY):
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required; run: make setup PYTHON=python3.12")'
	$(PYTHON) -m venv $(VENV)

.env:
	cp .env.example .env
	@echo "Created .env from .env.example"

setup: $(PY) .env  ## Create .venv, install the exact locked packages, create .env
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.lock
	$(PY) -m pip install --quiet --no-deps -e .
	@echo "Setup done. Next: make db"

lock:  ## Re-resolve requirements.lock (needs uv), then run tests on Python 3.10 and 3.12
	bash scripts/lock.sh

doctor:  ## Check every prerequisite and print fixes
	@if [ -x $(PY) ]; then $(PY) -m cpgos.doctor; else $(PYTHON) -m cpgos.doctor; fi

db: .env  ## Start Docker (Colima) and Postgres, then apply migrations
	bash scripts/dev_db.sh
	$(MAKE) migrate

migrate: $(PY)  ## Apply database migrations
	$(PY) -m cpgos.db

stop:  ## Stop Postgres (data is kept)
	docker compose stop db

download: $(PY)  ## Download a dataset: make download DATASET=m5
	$(PY) scripts/download_data.py $(DATASET)

load-m5: $(PY)  ## Load real M5: daily to Parquet, weekly to Postgres
	$(PY) scripts/load_data.py m5

test: $(PY)  ## Run the backend tests (needs a reachable Postgres)
	$(PY) -m pytest

api: $(PY)  ## Run the API (port from .env, default 8100)
	bash scripts/serve.sh api

web:  ## Run the UI (port from .env, default 3100)
	bash scripts/serve.sh web
