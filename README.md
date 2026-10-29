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
| 1 | Data foundation and product shell | Data foundation done, shell in progress |
| 2 | Document AI and reconciliation | Not started |
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
- **Real M5 loader** (staging + COPY, idempotent, provenance-preserving).
- **Published KeHE deduction codes** with a source link per row.
- **Tests against a real Postgres**, run in CI.

See [`docs/decisions.md`](docs/decisions.md) for the reasoning behind each choice.

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

```bash
docker compose up -d
pip install -e ".[dev]"
export CPGOS_DATABASE_URL=postgresql://cpgos:cpgos_local_only@localhost:5432/cpgos
python -m cpgos.db                       # apply migrations

python scripts/download_data.py --list   # datasets and license status
echo "m5: true" >> data/license_acceptance.yaml   # only after reading the terms
python scripts/download_data.py m5       # needs Kaggle CLI + joined competition
```

Tests:

```bash
export CPGOS_TEST_ADMIN_URL=postgresql://cpgos:cpgos_local_only@localhost:5432/postgres
pytest
```

## Limitations (current)

- No UI, models, or agents yet; this phase is the foundation they depend on.
- The M5 loader holds one file in memory; a streaming version is needed before
  loading all ~30k series on a small machine.
- Several dataset licenses are flagged `license_verified: false` in the registry
  and must be read before use.
