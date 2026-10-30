# One entry point for every task. Every Python command runs with the project's
# own interpreter (.venv), never whatever `python` your shell happens to have.

PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
DATASET ?= m5

.DEFAULT_GOAL := help
.PHONY: help setup lock doctor db migrate stop download load-m5 finance-world reconcile eval-recon load-fatura train-router eval-router route-docs learn-rules extract-docs eval-extract test api web

help:  ## List commands
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

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

finance-world: $(PY)  ## Build the real-anchored financial world on loaded M5 (once per database)
	$(PY) scripts/finance.py world

reconcile: $(PY)  ## Cash application + deduction validation, written by the engine role
	$(PY) scripts/finance.py reconcile

eval-recon: $(PY)  ## Replay both engines, score against the hidden answer key, store the report
	$(PY) scripts/finance.py evaluate

load-fatura: $(PY)  ## Register FATURA invoices and write their answer key (after make download DATASET=fatura)
	$(PY) scripts/documents.py load-fatura

train-router: $(PY)  ## Learn layout fingerprints; choose grid and threshold on calibration layouts
	$(PY) scripts/documents.py train-router

eval-router: $(PY)  ## Score the frozen layout router once on held-out invoices
	$(PY) scripts/documents.py eval-router

route-docs: $(PY)  ## Route every document to a known layout or "new", as the engine role
	$(PY) scripts/documents.py route

learn-rules: $(PY)  ## Learn field areas and label vocabulary from the fit layouts
	$(PY) scripts/documents.py learn-rules

extract-docs: $(PY)  ## OCR + rules on every document not yet extracted (needs Tesseract)
	$(PY) scripts/documents.py extract

eval-extract: $(PY)  ## Score the frozen rules on known-layout test invoices and the holdout layouts (needs Tesseract)
	$(PY) scripts/documents.py eval-extract

test: $(PY)  ## Run the backend tests (needs a reachable Postgres)
	$(PY) -m pytest

api: $(PY)  ## Run the API (port from .env, default 8100)
	bash scripts/serve.sh api

web:  ## Run the UI (port from .env, default 3100)
	bash scripts/serve.sh web
