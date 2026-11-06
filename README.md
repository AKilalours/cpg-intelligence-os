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
| 2 | Document AI and reconciliation | **Reconciliation done** (2a); document AI measured on FATURA invoices and CORD receipt photos (2b); review UI next |
| 3 | Agentic investigation | **Investigator scored** (Texas and Wisconsin, once); case screen and number-checked letters done; fine-tuned code model next |
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
| Wrong automatic cash postings | **0 of 868** (95% CI 0.0% to 0.4%) | 5 of 876 (exact reference only) |
| Cash dollars posted correctly with no person | **89.5%** (90.2% before matches with a same-amount twin invoice went to a person, decision 72) | 88.4% |
| Cash lines left with no suggestion | **0** | 108 |
| Deductions on imperfect evidence (report region): invalid claims approved | **0 of 39** | |
| Valid claims disputed | 2 of 121 (calendar gaps the Phase 3 agent will read past) | |
| Recovered at 1 review a week | **$3,408.41 (all that unlimited reviews recover)** | $2,542.13 largest first, $214.14 oldest first |

World generator 1.2 adds the two remittance patterns fix 5 handles: invoices paid net of a claim on the
same line, and claims taken twice. Scored in a side database (`make world-sidecar`) with the same
imperfect evidence as above, so the numbers compare like for like:

| World 1.2 | This engine | Baseline |
|---|---|---|
| Short-pays explained on the remittance line | **33 of 33 posted automatically and right**, 0 wrong | |
| Wrong automatic cash postings | **0 of 861** | 10 of 868 |
| Cash dollars posted correctly with no person | **89.3%** | 87.0% |
| Held for a person because another open invoice has the same amount | 9 lines | |
| Duplicate claims (double-dips) rejected | **4 of 4**; each now lists the claim it repeats (decision 74; re-measure with `make real-run`) | |
| Valid claims disputed (report region) | 2 of 121, as in world 1.1 | |

The deduction checker is scored on deliberately imperfect evidence (missing
POS weeks, unscanned receipts, calendar typos), tuned on California stores
and reported on Texas and Wisconsin. The damage is an overlay decided by
business keys, so every machine gets the same damage and the same score
(world and damage fingerprints are printed to prove it).

```bash
make finance-world   # once per database, after make load-m5 (~20 s)
make reconcile       # cash application + deduction validation, as the engine role
make eval-recon      # replay both engines, score against the answer key, store the report
```

## Phase 3 so far: the investigation agent (measured)

A read-only agent goes through the engine's work and accepts, disputes with its arithmetic, or asks
the retailer for one named document. It sees only what a supplier really has (the damaged evidence,
invoices, terms, shelf prices), enforced by database grants, never the truth under the damage.
Designed on California; scored once on Texas and Wisconsin (report 28, investigator 1.1).

| Texas and Wisconsin, 170 claims | Agent | Engine alone |
|---|---|---|
| Valid claims disputed | **0 of 38** | 2 of 41 |
| Invalid claims approved | **0 of 119** | 0 of 119 |
| Disputes exact to the cent | **38 of 38** | |
| Claims left undecided | 13 named document requests (12 of those claims were valid) | 10 "needs evidence" |
| Cost | 1 real over-claim asked about instead of disputed (a slower dispute, not a lost one) | |

**Dispute and request letters** are drafted by a local model (qwen2.5-VL 7B through Ollama, no per-call
cost) and used only if two deterministic checks pass: every number and date must trace to the case,
and what the letter says about the dispute deadline must be true on the ledger date. Otherwise the
template letter is used and the case screen says why.

| 105 open dispute and request letters | Prompt 1.1 | Prompt 1.2 |
|---|---|---|
| Every number and date traceable | 105 of 105 | 105 of 105 |
| Deadline described truthfully | **93 of 105** (12 asked for action by a date already past) | **104 of 105** |
| Used as written | 93 | 104; the 1 failure falls back to the template |

Prompt 1.3 gives the model the one true deadline sentence to copy and rejects wording that echoes the
instructions: **105 of 105** letters pass all three checks.

**Reading deduction reasons** (addition 12). Free-text reasons decide which check runs; KeHE's IA code
covers shortages, late fees, and price differences alike. Generated from industry vocabulary with
abbreviations and keying errors; scored on phrase families no reader saw in training ("OTIF FINE" for a
late fee, "SWELL ALLOWANCE" for spoils). Generated text, not real remittances.

| Reader | Seen phrasings (400) | Unseen phrasings (800) | Unseen macro-F1 |
|---|---|---|---|
| Keyword rules | 84.8% | 26.5% | 0.34 |
| Character n-gram Naive Bayes | **100%** | 50.5% | 0.50 |
| Local model qwen2.5-VL 7B, definitions only | 90.5% | **70.9%** | 0.70 |
| LoRA fine-tune of Qwen2.5-0.5B | 99.8% | 45.9% | 0.44 |

The fine-tune does not ship: it is worse on unseen wording than plain Naive Bayes. Its training loss
reached almost zero by step 100 of 750; it memorised the training phrasings, and a 0.5B model lacks the
domain knowledge (that "swell" means damaged goods) that the 7B model brings. Next: Naive Bayes when it
is confident, the 7B model otherwise, with its threshold chosen on the first unseen set (now a
development set) and scored once on a second unseen set written before any reader saw it.

Both engine false disputes came from damaged records: a promotion missing from the calendar, and a
promotion end date keyed a week early. In both, the shelf price showed the promotion was running,
so the agent asked for the agreement instead of disputing. A price drop can stop a dispute; it can
never approve a claim. See [docs/phase3_agent.md](docs/phase3_agent.md).

## Phase 2b so far: document intelligence (measured)

On FATURA (10,000 invoices, 50 layouts; 10 layouts held back and scored once):

| | Result |
|---|---|
| Answer key | 76,194 typed values, 0 unparsed, hidden from the engine role |
| Known layouts recognised, scan-like copies | **795 of 800**, 0 sent to a wrong layout |
| New layouts flagged as new, clean and scan-like | **4,000 of 4,000** |
| Tier 1 (OCR + rules), known layouts: precision / coverage | **99.2% / 99.1%** |
| Tier 1, never-seen layouts: precision / coverage | **98.1% / 84.0%** (seller names left to the models) |
| Full cascade on never-seen layouts: complete invoices with no human needed | **69.5%** of 210 (rule 1.4, round 5, scored once); all 90 invoices missing a required field sent to a person |
| Accepted values that were correct (round 5) | **3,662 of 3,666 (99.89%, CI 99.72-99.96%)**; $0.0044 per invoice |
| Rule 1.5 (accept names two models agree on) | **Rejected on evidence**: 728 of 732 right on development rounds, lower bound 98.6% below the 99.72% bar; the misses are the other party printed on the page (buyer vs bill-to), which a page check cannot catch (decision 73) |
| Fields a reviewer checks per invoice | 0.78 (was 0.98 under rule 1.3) |

On CORD (real photographed receipts, line items; rule frozen on validation, test scored once):

| | Result |
|---|---|
| Answer key | 998 receipts, 4,047 totals values, 2,570 line items; 2 duplicate photos dropped; labels add up on 947 of 986 |
| Receipts straight through (test) | **71 of 100** |
| Every amount right when accepted (test) | **65 of 71 (91.5%, 95% CI 83-96%)**; validation was 80 of 83 |
| Of the 6 wrong: | 1 hole in my check (fixed, untested on fresh data), 1 label error, 2 skipped payment lines, 2 answer-key gaps |

Details, rejected experiments, and caveats: [`docs/phase2b_documents.md`](docs/phase2b_documents.md).

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
