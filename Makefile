# One entry point for every task. Every Python command runs with the project's
# own interpreter (.venv), never whatever `python` your shell happens to have.

PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
DATASET ?= m5
ROUND ?= 2
SPLIT ?= validation

.DEFAULT_GOAL := help
.PHONY: help setup lock doctor db migrate stop download load-m5 load-cord decide-docs regress-docs export-regressions names-analysis receipts-collect receipts-eval finance-world finance-evidence finance-restore-evidence real-run world-sidecar agent-design agent-score agent-report agent-seeds letters-local stp-simulate stp-frontier stp-fresh stp-status codes-baselines codes-prompted setup-lora codes-lora codes-hybrid codes-paired codes-report reconcile eval-recon load-fatura train-router eval-router route-docs learn-rules extract-docs eval-extract vlm-dev eval-cascade vlm-collect routing-estimate routing-calibration routing-holdout test api web

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

finance-evidence: $(PY)  ## Make deduction evidence realistically imperfect (an overlay; records untouched)
	$(PY) scripts/finance.py evidence

finance-restore-evidence: $(PY)  ## Repair records altered by evidence-1.0.0 (rebuilds the world in a temp database to compare)
	$(PY) scripts/finance.py restore-evidence

real-run:  ## Every real-database step in order (no model spend: replay only); output saved in local_runs/
	bash scripts/real_run.sh

world-sidecar: $(PY)  ## Build and score the current world generator in a side database (main database untouched; FRESH=1 rebuilds it)
	FRESH=$(FRESH) $(PY) scripts/sidecar_world.py

agent-design: $(PY)  ## Phase 3 agent on California only: where it differs from the engine, with the truth (rerunnable)
	$(PY) scripts/finance.py agent-design

agent-score: $(PY)  ## Phase 3 agent scored once on Texas and Wisconsin per agent version; saves cases and reasoning
	$(PY) scripts/finance.py agent-score

agent-report: $(PY)  ## Print the latest stored agent report again (no scoring)
	$(PY) scripts/finance.py agent-report

letters-local: $(PY)  ## Local model ($0) drafts dispute and request letters; only drafts whose every number traces are used (LIMIT=n)
	LIMIT=$(LIMIT) $(PY) scripts/finance.py letters-local

stp-simulate: $(PY)  ## Addition 11: replay the world's cash lines with the error budget (healthy, silent fault, bad release)
	$(PY) scripts/stp.py simulate

stp-frontier: $(PY)  ## Addition 11: 200 rolls per scenario at audit rates 1/5/10/20% on the main world; picks the rate by a fixed rule
	$(PY) scripts/stp.py frontier

stp-fresh: $(PY)  ## Addition 11: the picked audit rate, scored once on 8 fresh worlds (seeds 201-208)
	$(PY) scripts/stp.py fresh

stp-status: $(PY)  ## Addition 11: each posting lane's reviews, errors, upper bound, and whether it may post
	$(PY) scripts/stp.py status

codes-baselines: $(PY)  ## Addition 12: keyword rules and n-gram Naive Bayes on seen and unseen reason phrasings
	$(PY) scripts/codes.py baselines

codes-prompted: $(PY)  ## Addition 12: the local model with category definitions only (Ollama, $0)
	$(PY) scripts/codes.py prompted

setup-lora: $(PY)  ## Install the LoRA training stack into .venv (torch, transformers, peft); not needed otherwise
	$(PY) -m pip install --quiet "torch>=2.4" "transformers>=4.45" "peft>=0.13" "accelerate>=0.34"

codes-lora: $(PY)  ## Addition 12: fine-tune Qwen2.5-0.5B with LoRA on this Mac ($0), then score it once
	@command -v ollama >/dev/null && ollama stop $${CPGOS_OLLAMA_MODEL:-qwen2.5vl:7b} 2>/dev/null; true   # free its memory first
	$(PY) scripts/codes_lora.py && $(PY) scripts/codes.py score-lora

codes-hybrid: $(PY)  ## Addition 12: Naive Bayes when confident, else the local model; scored on the second unseen set
	$(PY) scripts/codes.py hybrid

codes-paired: $(PY)  ## Addition 12: readers compared on the same unseen phrasings, exact paired test (replayed, $0)
	$(PY) scripts/codes.py paired

codes-report: $(PY)  ## Addition 12: every reader's score side by side
	$(PY) scripts/codes.py report

agent-seeds: $(PY)  ## The frozen agent vs the engine on fresh worlds (SEEDS=8), claim by claim, exact paired test ($0)
	SEEDS=$(SEEDS) KEEP=$(KEEP) $(PY) scripts/agent_seeds.py

reconcile: $(PY)  ## Cash application + deduction validation, written by the engine role
	$(PY) scripts/finance.py reconcile

eval-recon: $(PY)  ## Replay both engines, score against the hidden answer key, store the report
	$(PY) scripts/finance.py evaluate

load-cord: $(PY)  ## Register CORD receipt photos, write their answer key, audit the labels (after make download DATASET=cord)
	$(PY) scripts/documents.py load-cord

receipts-collect: $(PY)  ## Local model reads CORD receipts (SPLIT=validation|test); prints no scores
	$(PY) scripts/documents.py receipts-collect --split $(SPLIT)

receipts-eval: $(PY)  ## Claude for receipts that do not add up, then score (SPLIT=validation|test)
	$(PY) scripts/documents.py receipts-eval --split $(SPLIT)

decide-docs: $(PY)  ## Store routing decisions for the review screens (ROUND=n holdout sample; replays recordings)
	$(PY) scripts/documents.py decide --split holdout --round $(ROUND)

regress-docs: $(PY)  ## Replay the routing rule on every value a person confirmed; fails if one is now accepted wrong
	$(PY) scripts/documents.py regress

export-regressions: $(PY)  ## Write confirmed FATURA values to tests/fixtures so the test suite replays them in CI
	$(PY) scripts/documents.py export-regressions

names-analysis: $(PY)  ## Rule 1.5 question on development rounds 1-4: accept names two models agree on? (no model calls)
	$(PY) scripts/documents.py names-analysis

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

vlm-dev: $(PY)  ## Local vision model (Ollama) on 20 dev invoices at two image scales (needs ollama serve)
	$(PY) scripts/documents.py vlm-dev

eval-cascade: $(PY)  ## Tier 1 + local model on fixed samples, scored (about 1.5 h first time; resumable)
	$(PY) scripts/documents.py eval-cascade

vlm-collect: $(PY)  ## Tier 1 + local model on fresh round-2 samples, no scores shown (about 1.5 h; resumable)
	$(PY) scripts/documents.py vlm-collect --round $(ROUND)

routing-estimate: $(PY)  ## How many Claude calls the routing steps need and what they cost (no calls)
	$(PY) scripts/documents.py routing-estimate

routing-calibration: $(PY)  ## Two-readers rule with Claude as third reader, on calibration invoices (paid, capped)
	$(PY) scripts/documents.py routing --split calibration

routing-holdout: $(PY)  ## The one-time final score on fresh holdout invoices (paid, capped)
	$(PY) scripts/documents.py routing --split holdout --round $(ROUND)

test: $(PY)  ## Run the backend tests (needs a reachable Postgres)
	$(PY) -m pytest

api: $(PY)  ## Run the API (port from .env, default 8100)
	bash scripts/serve.sh api

web:  ## Run the UI (port from .env, default 3100)
	bash scripts/serve.sh web
