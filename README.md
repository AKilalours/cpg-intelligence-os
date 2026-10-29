# CPG Intelligence OS
### Evidence-grounded AI for financial operations and commercial planning

**Problem.** CPG businesses operate across fragmented financial documents, retailer
transactions, promotional records, and sales data. Inconsistent identifiers,
unfamiliar document layouts, and uncertain evidence make automated decisions
difficult to trust.

**Solution.** An independent applied AI platform that converts heterogeneous
business records into validated financial data, investigates exceptions through
controlled agentic workflows, and connects financial operations to demand
forecasting and promotion analytics.

> Independent portfolio project. Not affiliated with any company. No customer
> relationships, production deployments, or performance improvements are claimed
> without reproducible evidence in this repo.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Data foundation and product shell | **Done**, verified on full real M5 |
| 2 | Document AI and reconciliation | **Reconciliation done** (2a); document AI next (2b) |
| 3 | Agentic investigation | Not started |
| 4 | Evaluation and production controls | Not started |
| 5 | Forecasting and promotion analytics | Not started |
| 6 | Product polish and technical proof | Not started |

## What exists today

- **Canonical CPG data model** in Postgres: brands, retailers and distributors,
  stores, SKUs, invoices, payments, remittance lines, cash applications,
  deductions, deduction codes, promotions, sales observations.
- **Provenance on everything:** dataset versions (SHA-256), ingestion runs with
  code version, quarantined rows with reasons, source documents, evidence
  references, append-only calculations and audit events.
- **Ledger protection in the database:** the agent role cannot modify financial
  records; only the reviewer role can change decision statuses.
- **Exact money arithmetic** with no-lost-cent allocation.
- **Data contracts** with row quarantine and batch rejection on format drift.
- **Real M5 loader** that streams 46.9M rows within a fixed memory budget:
  daily rows to Parquet, weekly totals to Postgres, idempotent, provenance-preserving.
- **Published KeHE deduction codes** with a source link per row.
- **Read-only API** (FastAPI) over the schema: overview, datasets, ingestion
  runs, deductions, sales series. Money is returned as exact decimal strings.
- **Product shell** (Next.js) with the six sections of the plan: Overview,
  Documents, Finance, Planning, AI Investigation, Evaluation Lab. Every number is
  computed from the database; empty states say exactly what to load.
- **Tests against a real Postgres** plus web type checks and build, run in CI.

See [`docs/decisions.md`](docs/decisions.md) for the reasoning behind each choice.

## Phase 1 results (measured)

The full M5 dataset was loaded store by store in a 4-core, 3.9 GB Linux sandbox
with DuckDB capped at 1 GB, then recounted by independent code:

| Check | Series | Daily rows after pre-launch filter | Units |
|---|---|---|---|
| Independent Python recount | 30,490 | 46,881,677 | 66,927,173 |
| Parquet output (daily) | 30,490 | 46,881,677 | 66,927,173 |
| Postgres output (weekly, 6,719,161 rows) | 30,490 | | 66,927,173 |

- Rows quarantined: 0. Pre-launch days excluded: 12,299,413 (30,490 series x 1,941 days, minus loaded rows).
- Per-store load time: 35 to 46 seconds. Peak memory: 1.07 to 1.21 GB, flat across stores.
- Verified file fingerprints (SHA-256) for the M5 files used:
  - `calendar.csv` `568d0fe5f41790142379698732908e4e57432c1c6396f3f59fb880a9c2b54231`
  - `sell_prices.csv` `5c7cf045b1abbdc8d5007809c40b3682c5216075937f93e2cef01caddc4a30d9`
  - `sales_train_evaluation.csv` `c21a519596680feb86f27a9e62f6c8b583f8be60c2c195f080ae8ca2990af2b7`

Bugs found by running on real data, each with a regression test, are written up
in [`docs/decisions.md`](docs/decisions.md) (entries 14, 16, 17, 20).

### Phase 1 release checklist

| Check | Status |
|---|---|
| Every stored value traces to a source file version and loader code version | Done |
| Bad rows quarantined with reasons; format changes reject the batch | Done |
| Partial loads labeled partial everywhere | Done |
| Test data cannot be counted or stored as real data | Done, with tests |
| Ledger cannot be changed by the agent role (database-enforced) | Done, with tests |
| No hardcoded metrics in the UI | Done |
| CI runs backend tests, web type check, and web build | Done |
| Full real dataset processed within a fixed memory budget | Done (M5) |

## Phase 2a results: reconciliation (measured)

On a financial world generated on real M5 sales and published KeHE terms
(1,040 invoices, 984 cash lines, 312 deductions; answer key hidden from the
engines by a database role). Details and caveats:
[`docs/phase2a_reconciliation.md`](docs/phase2a_reconciliation.md).

| | This engine | Baseline |
|---|---|---|
| Wrong automatic cash postings | **0 of 875** (95% CI 0.0% to 0.4%) | 5 of 876 (exact reference only) |
| Cash dollars posted correctly with no person | **90.2%** | 88.4% |
| Cash lines left with no suggestion | **0** | 108 |
| Deduction recoverable amounts exact to the cent | 312 of 312 | |
| Recovered at 1 review a week | **$3,429.77 (98%)** | $2,542.13 largest first, $214.14 oldest first |

The deduction validator applies the same terms the world was built from, so
its perfect score proves the evidence lookups and arithmetic, not that messy
real claims are solved; that is what Phases 2b and 3 are for.

```bash
make finance-world   # once per database, after make load-m5 (~20 s)
make reconcile       # cash application + deduction validation, as the engine role
make eval-recon      # replay both engines, score against the answer key, store the report
```

## Data

All data comes from public sources, downloaded locally and never committed.

| Dataset | Used for | Origin |
|---|---|---|
| M5 (Walmart) | Forecasting | real |
| Corporacion Favorita | Forecasting, promotions | real |
| DocILE | Document AI, agent eval | real (gated, request form) |
| FATURA | Unseen-layout evaluation | template-generated |
| CUAD | Agreement clause extraction | real |
| Open Food Facts + USDA Branded Foods | Entity resolution ground truth (GTIN join) | real |
| dunnhumby Breakfast at the Frat, Complete Journey | Promotion analytics | real |
| KeHE deduction code prefixes | Deduction taxonomy | real, published |

**Known gap:** no public dataset links real remittances and deductions to real
invoices. Those events are generated on top of real documents, real sales, and
published deduction rules, labeled `real_anchored_generated`, and reported
separately.

## Quick start

Requirements: Python 3.10+, Docker (Colima or Docker Desktop), and for the UI Node 22
(pinned in `web/.nvmrc`; `make web` switches to it automatically if nvm is installed).
Run each line on its own; every command checks its prerequisites and says what to fix.

```bash
make setup        # creates .venv, installs packages, creates .env
make db           # starts Docker (Colima) if stopped, starts Postgres, applies migrations
make doctor       # checks everything and prints the exact fix for anything missing
```

Load real M5 (after reading the Kaggle terms):

```bash
echo "m5: true" >> data/license_acceptance.yaml
make download DATASET=m5    # ~50 MB, extracts the 3 files used
make load-m5                # one store at a time (~40s each); re-run resumes where it stopped
```

Each store writes a temporary file of about 250 MB while it loads.

Run the app (two terminals) and the tests:

```bash
make api          # http://localhost:8100/api/health
make web          # http://localhost:3100
make test
```

Ports default to 3100 (UI) and 8100 (API) because 3000 and 8000 are often taken,
for example by Grafana. Change them in `.env` (`CPGOS_WEB_PORT`, `CPGOS_API_PORT`);
`make web` and `make api` refuse to start on a busy port and name the program using it.

All Python commands run through the project's own `.venv`, so a conda or system
Python in your shell can't be picked up by accident. `make help` lists everything.

## Limitations (current)

- No learned models or agents yet: reconciliation is rules plus evidence, by
  design, so later model and agent work has a measured baseline to beat.
- Payments and deductions are generated (labeled `real_anchored_generated`);
  no public dataset links real remittances to real invoices.
- Several dataset licenses are flagged `license_verified: false` in the registry
  and must be read before use.
