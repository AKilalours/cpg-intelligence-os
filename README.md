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

**Eight more fresh worlds** (report 37, `make agent-seeds`): the same frozen agent and engine on 8
worlds neither had seen, 2,946 claims, compared claim by claim.

| 8 fresh worlds, 2,946 claims | Agent | Engine alone |
|---|---|---|
| Valid claims disputed | **1 of 807** (0.12%, 95% CI 0.02 to 0.7%) | 24 of 833 (2.9%, 1.9 to 4.25%) |
| Invalid claims approved | 1 of 1,890 | 1 of 1,869 (the same claim) |
| Paired on the same claims | wrong where the engine was right: 1 | wrong where the agent was right: **24** |
| Exact McNemar test | **p = 0.000002** | |
| Cost | 5 real invalid claims asked about instead of disputed | |

All 24 engine false disputes became document requests, none an acceptance. The one claim both
approved and the agent's one false dispute are explained in decision 92; neither was tuned on, since
these worlds are now spent.

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

| Reader | Seen phrasings | Unseen set 1 (development) | **Unseen set 2** (written before any score, scored once) |
|---|---|---|---|
| Keyword rules | 84.8% | 26.5% | 27.6% (95% CI 24.6 to 30.8) |
| Character n-gram Naive Bayes | **100%** | 50.5% | 62.6% (59.2 to 65.9) |
| Local model qwen2.5-VL 7B, definitions only | 90.5% | 70.9% | 82.8% (80.0 to 85.2) |
| LoRA fine-tune of Qwen2.5-0.5B | 99.8% | 45.9% | not run: does not ship |
| **Hybrid: Naive Bayes when confident, else the 7B model** | **100%** | 72.9% | **85.2%** (82.6 to 87.5) |

The hybrid is the reader to use: perfect on familiar wording with no model call, and significantly
better than the 7B model on new wording. The intervals overlap, but paired on the same 800 phrasings
the hybrid was right where the 7B model was wrong 21 times and the reverse happened once (exact
McNemar p = 0.00001, `make codes-paired`). Its threshold was chosen
on unseen set 1; unseen set 2 was committed before any reader was scored on it. Set 2 is easier for
every reader (more of its words also appear in training), so compare readers within a column. The LoRA
fine-tune memorised the training phrasings (loss near zero by step 100 of 750) and is worse on unseen
wording than Naive Bayes; a 0.5B model lacks the domain knowledge the 7B model brings.

Both engine false disputes came from damaged records: a promotion missing from the calendar, and a
promotion end date keyed a week early. In both, the shelf price showed the promotion was running,
so the agent asked for the agreement instead of disputing. A price drop can stop a dispute; it can
never approve a claim. See [docs/phase3_agent.md](docs/phase3_agent.md).

## Phase 4 so far: automatic posting only where it is earned (measured)

Addition 11. Like a new hire in accounts receivable: every match is checked at first, they post on their
own once enough checks come back clean, a manager keeps spot-checking, and mistakes put them back on full
checks. Each kind of automatic match is a lane. A lane posts with no person only when people's reviews of
its suggestions, for this engine version, put the 95% upper bound on its error rate under 1% (299 clean
reviews). Open lanes still send 1 in 5 to a person; a wrong posting a person reverses stops the lane; a new
engine version starts from zero. **Postgres enforces it** (migration 0021): the engine cannot post around
the gate, see which lines will be checked, or change the policy, and every policy change is audited.

The spot-check rate was chosen on the main world by a rule written before any result (decision 94), then
scored once on 8 worlds built for it (report 40). Each scenario is replayed 200 times with its own sample
and fault, because one replay is one roll of the dice. The reviewer is modelled as perfect.

| 8 unseen worlds, 7,019 lines the engine would post | Wrong postings, no gate | With the gate, typical (bad roll) |
|---|---|---|
| Healthy history | 0 | **0** (0) |
| A silent fault: 5% of the busiest lane turns wrong | 126 | **30** (49) |
| A silent fault: 20% turns wrong | 503 | **38** (56) |
| A bad release: new version, 10% wrong | 252 | **0** (0) |

The cost is real. With no gate, 89.5% of dollars post with no person on the main world (and nothing checks that). With the gate,
44.7% do on healthy history across the unseen worlds (49.4% on the main world), because each lane must first earn its 299 clean reviews: over a two-year
world, most of the human work is that earning, not the spot checks (on the main world, raising the spot
check from 1% to 20% added about 100 reviews out of 868 lines). Two of the three lanes (explained short-pays,
one-keystroke typos) never see 299 lines in a world this size, so they stay fully checked; the evidence says
nothing about them yet, and the gate acts accordingly. A quiet 5% fault is still caught slowly at this
volume (all faulty lanes closed in 133 of 200 rolls, a median 71 lines after it starts): detection speed is
set by how many lines get checked, so at a real client's hundreds of lines a day the same fault closes the
lane in days. The budget counts lines, not dollars.

### Drift monitors: a smoke alarm on the inputs

Spot checks find a silent fault only after dozens of lines. Many faults show in the inputs first: a payer
quoting PO numbers instead of invoice numbers, a wave of short-pays, reasons worded in ways the reader has
never seen, a price war. Weekly, five signals that should hold steady whatever the business does
(reference format, how lines match, short-pays, whether the reason reader is sure, shelf-price drops) are
compared with the baseline, each value with an exact two-sample test. An open cash alarm sends every
would-be automatic posting to a person until someone clears it, with their name and what they checked;
the baseline then restarts after the change they accepted. The monitor role cannot clear alarms, post cash
or change policy.

Chosen on 8 development worlds by a rule written in advance (report 46), then scored once on 8 unseen worlds
(report 47). Each change was injected into the raw inputs and the real engine rerun on them, 80 rolls each.

| 8 unseen worlds | Result |
|---|---|
| False alarms on healthy history | **0** cash and **0** claims in about 9 world-years each; 2 on prices in 25 |
| Payer switches a quarter of lines to PO numbers | 80 of 80 caught, median 14 days (28 lines) |
| A wave of short-pays | 80 of 80, 21 days |
| Partial payments on 30% of lines | 77 of 80, 35 days |
| Half the reasons in wordings never seen | 76 of 80, 48 days |
| A price war on 30% of items | 80 of 80, at the first weekly check |

It passes its bar (95% caught within 120 days, at most one false alarm per stream per world-year) with two
changes at the edge (96% and 95%). It took four designs, and the first three are in the record: 1.0 alarmed in
one healthy cash week in four on unseen worlds; 1.1 rested on a wrong diagnosis and was no better; 1.2 fixed
the statistics (each value now tested exactly, the baseline treated as a sample) but missed the detection bar
on development worlds, so the scoring command refused to run. What changed in 1.3, all on development worlds:
signals that move with sales growth and promotion seasons (payment size, reason mix) moved to anomaly
detection, and the reference format kept its prefix, because invoice and PO numbers had looked identical. The
price false alarms come from the same real M5 weeks in every world, so they are not independent, and the cash
and claim streams are generated and steadier than a real customer's: production will see more. See decisions
97 to 102.

## Phase 5 so far: demand forecasting (measured, real data)

Real M5 sales as 30,490 item-store weekly series (52% smooth, 34% intermittent, 8% lumpy, 6% erratic),
forecast 8 weeks ahead. Rolling-origin backtests on 4 development origins, with the test weeks cut off in
code; the last 8 weeks (28 March to 22 May 2016) scored once, after the model and the rule for choosing it
were committed. Metric: M5's RMSSE, weighted by each series' dollar sales.

| Last 8 weeks, 30,490 series (scored once) | WRMSSE (lower is better) | Bias |
|---|---|---|
| Seasonal naive (same week last year) | 1.363 | -13.5% |
| Naive (last week) | 1.031 | -4.8% |
| Croston / SBA / TSB (intermittent-demand methods) | 0.917 / 0.920 / 0.898 | |
| Exponential smoothing, rate chosen per series | 0.894 | -3.5% |
| **Global LightGBM** (Tweedie; planned price, calendar, growth) | **0.855** | -3.5% |

LightGBM is 4.4% better than exponential smoothing (paired 95% bootstrap interval 3.6% to 5.2%), 4.7% better
than TSB, and 37% better than seasonal naive, and best in three of four demand classes (a combination edges it
on lumpy items). What the development origins taught, before the test: per-series best-fit selection
overfits (chosen on 24 weeks per series it lost to using one method everywhere, 0.968 against 0.918); the
gain over smoothing came from what a brand knows in advance (its planned price and the calendar), not from
the model alone; and the more elaborate selection strategies were not proven better than one global model,
so the simpler one ships. One function builds features for training and forecasting, and tests prove no
future sales reach a feature. Stated: M5's weekly sell price stands in for a brand's promotion plan; no
Prophet or ARIMA; a small under-forecast remains. See decision 103 and reports/forecast-1.0.0_test.json.

### Promotions: will a price cut pay back? (addition 10)

Before showing anyone a lift prediction, I tested whether one could be trusted. On 23,080 real M5 price cuts,
each compared with the same item at stores that did not cut that week (so holidays cancel), the typical cut
did nothing (median -2.3%, quartiles -51% and +78% for 10-15% cuts), and deep cuts mostly preceded zero sales
(clearances, stock-outs). The forecaster's price response predicted +13% where development cuts delivered
-8.7%. Scored once on the 167 cuts in the test weeks, by a rule committed first:

| Estimate of a cut's lift | Error on 167 unseen cuts |
|---|---|
| Assume no lift | 0.986 |
| Median of earlier cuts, same department and depth | 0.955 (not significantly better) |
| The ML forecaster | **1.035 (worse than assuming nothing)** |

So the Promotions page shows no lift prediction. It shows what a brand can trust: the break-even arithmetic
(at a 30% margin a 20% cut needs +200% units) drawn as a curve through every real controlled cut, and the
share of cuts like yours that cleared it (15% of 656 for 20% off at 30% margin in FOODS 3). Drag the cut and the
margin, tap a dot to simulate that item's next 8 weeks, tap weeks to promote them. Correlation in observational
price data is not a promotion response; a real one needs a brand's promotion calendar or an experiment.

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
