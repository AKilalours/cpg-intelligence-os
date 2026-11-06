# Engineering decisions (Phase 1)

Short records of choices a reviewer might question, and why.

## 1. Postgres is the single source of truth
Finance, Sales, and Ops must see the same number. One relational store with
foreign keys makes "the same number" enforceable instead of a convention.

## 2. Ledger safety is enforced by the database, not by prompts
The `cpgos_agent` role can read everything and write only evidence, cases,
and audit events. A prompt-injected agent still cannot change an invoice or
deduction amount, because Postgres refuses the write
(`tests/test_db.py::test_agent_role_cannot_change_ledger`).

## 3. Exact decimal money, banker's rounding, no lost cents
Floats are rejected at the type level. Splitting a payment across invoices
uses the largest-remainder method so parts always sum to the original.

## 4. Raw and resolved values are stored separately
`remittance_line.raw_invoice_ref` is what the payer wrote (often wrong).
`cash_application` is what the system decided, with method, confidence, and
an explanation. Mixing them would make wrong matches unrecoverable.

## 5. External identifiers are not trusted
`partner_external_id` and `product_identifier` record how each link was made
(`exact`, `rule`, `model`, `human`) and with what confidence.

## 6. Data contracts with quarantine and batch rejection
Bad rows are kept with reasons. A batch with an abnormal failure rate is
rejected as a whole, because that pattern usually means the source format
changed. The rejection is still recorded, with its quarantine evidence.

## 7. Real data, honestly labeled
`dataset_version.data_origin` is one of `real`, `real_template_generated`,
`real_anchored_generated`, or `test_fixture`. Results are reported per origin.
No public dataset links real remittances and deductions to real invoices, so
those events will be generated on top of real documents, real sales, and
published deduction rules, and labeled `real_anchored_generated`.

## 8. M5 pre-launch zeros are dropped, promo flags are not invented
Days before an item's first listed price mean "not on the shelf", not "zero
demand". M5 has no promotion field, so `promo_flag` stays NULL until a derived
flag is added with `promo_flag_source = 'derived_price_drop'` (a DB check
forbids a flag without a source).

## 9. Append-only audit and calculations
History cannot be edited. A wrong calculation gets a new row that supersedes
it, so a reviewer can always see what the system believed at the time.

## 10. Migrations cannot be rewritten silently
Each applied migration's SHA-256 is recorded; editing one after it ran stops
the runner. Plain SQL files instead of an ORM migration tool keep the schema
readable in review.

# Phase 1b: product shell

## 11. The API is read-only by construction
Every request runs inside `SET TRANSACTION READ ONLY`, so even a buggy query
cannot write (`tests/test_api.py::test_api_connection_is_read_only`). Writes
will go through separate, explicitly permissioned endpoints when they exist.

## 12. Money never becomes a float, end to end
The API serializes Decimal as a string ("514.80"). The UI formats that string
by regrouping digits as text, never parsing it into a JavaScript number.
The only exception is chart drawing, where values are used for pixel positions
and never displayed.

## 13. No placeholder metrics
Every figure on screen is computed from database rows at request time. With
no data loaded, the UI shows zeros and the exact command to load data, rather
than sample numbers. Modules that do not exist yet say which phase builds them.

## 14. Fixtures never count as real data
Test fixtures share dataset keys with the real data they imitate. The API
excludes `test_fixture` rows when reporting loaded versions; a test enforces it
after an early build showed fixture loads as "3 versions loaded" for M5.

# Phase 1c: real-scale M5

## 15. Daily data in Parquet, weekly totals in Postgres
Real M5 is 46.9M daily rows after pre-launch filtering. Postgres is the system
of record for the ledger, not an analytics store. Daily rows go to Parquet
(partitioned by store, read with DuckDB for forecasting); Postgres gets weekly
totals (6.7M rows) for the app. Both come from one validated pass, and the
totals are checked to agree (66,927,173 units in both).

## 16. Memory is bounded by design, then measured
Two out-of-memory failures on real data shaped the loader:
1. DuckDB TEMP tables are memory-resident; work tables are now ordinary tables
   in a disk-backed scratch file, so they spill.
2. Scanning the 1,946-column wide file inside DuckDB exceeded 1 GB. The wide
   file is now reshaped in Python one line at a time, so memory holds one line.
Measured: 1.07 to 1.21 GB peak per store load with a 1 GB DuckDB limit,
flat across all 10 stores.

## 17. Read each source exactly once
A subset load once dropped about a third of its series: the sales file was a
view read twice, and with parallel reads the two reads returned different rows.
The file is now read once, and the loader enforces an invariant (every series
read appears in the output with every day) that fails the run if violated.

## 18. Large-file checks are SQL, and must agree with the Python contract
Row-by-row Python validation is too slow for 47M rows. The same rules are
expressed as SQL; a test damages a fixture four ways and asserts both
validators quarantine exactly the same rows for the same reasons.

## 19. Partial loads are labeled partial
Every ingestion run records its scope (`{"partial": true, "stores": [...]}`),
and the UI shows it. A one-store load can never pass for a full load.

## 20. Test output can never reach real data folders
A test once wrote fixture Parquet into data/processed/m5, where forecasting
would later read it as real data. Output paths now include the data origin,
the loader refuses to write fixtures under the real folder, and every test run
redirects that folder to a temporary one.

## 21. A source copy is verified, not trusted
M5 is downloaded from a GitHub-hosted copy of the Kaggle release. That copy is
reformatted (no `d` or `id` columns), so the loader supports both layouts and
derives day labels only after verifying the calendar dates are consecutive.
Row counts match the official release; cell values were not cross-checked
against Kaggle's own files, and the registry says so.

## 22. LLM tiers for Phase 2 (decided, not yet built)
Document extraction will cascade: rules and OCR, then a local open model via
Ollama, then Claude Sonnet 5.5 only for low-confidence fields and unseen
layouts. All models sit behind one interface so the Evaluation Lab, not
preference, decides what each tier uses.

# Setup reliability

## 23. One entry point, one interpreter, self-diagnosing
The first real setup on a Mac failed twice: Docker's engine (Colima) was
stopped, and because setup steps were chained with `&&`, the virtual
environment was never created. The next commands then ran with conda's base
Python, which lacked psycopg, and crashed with an import traceback.

Root cause: setup depended on five manual steps run in order, with the right
Python, Docker already running, and a variable exported in the same terminal.

Fix: a Makefile is the single entry point and always uses `.venv/bin/python`;
`make db` starts Colima or Docker Desktop if needed, checks the port is free,
and waits until Postgres is ready; the database address lives in `.env`
(created by `make setup`, real environment variables still win); scripts check
their packages before importing them and print the interpreter and the fix;
`make doctor` checks every prerequisite. A test runs a script under a Python
missing psycopg and asserts a plain message with the fix, not a traceback.

## 24. Load in partitions by default; never leave a run "running"
The first full single-pass M5 load on a Mac showed no progress after 9
minutes. Store-by-store loading was the configuration actually verified
(about 40 s and 1.1 GB per store), so it is now the default: memory is
bounded by one store, not the whole dataset, and a re-run skips stores
already loaded from the same file (matched by SHA-256 and granularity).

While fixing this, two gaps were closed: an interrupted load (Ctrl+C, crash)
left its run marked "running" forever, and two loads could run at once.
Interrupts now mark the run failed with a reason; a per-dataset advisory lock
allows one load at a time, and because of that lock the next load can safely
mark any leftover "running" record as interrupted. Each has a test.

## 25. Exact dependency versions, tested on the oldest and newest Python
DuckDB 2.0.0 (Python 3.11+ only) removed the `x -> ...` lambda syntax the
loader used. Development ran on Python 3.10, which can only install DuckDB
1.5.6, so the break was invisible locally while CI and the Mac (Python 3.12)
failed: a "works on my machine" bug caused by unpinned dependencies plus
testing on a different Python than users run.

Fixes: the SQL uses `lambda x: ...` (verified to need DuckDB 1.3+: 1.2.2
rejects it, 1.3.0 accepts it); `requirements.lock` pins exact versions with
per-Python markers (DuckDB 1.5.6 on 3.10, 2.0.0 on 3.11+) and is what
`make setup` and CI install; CI runs the suite on Python 3.10 and 3.12; a
weekly CI job installs the newest releases so the next break shows up as a
warning before it reaches anyone. One real M5 store was re-loaded on DuckDB
2.0.0 and matched the earlier verified counts exactly.

## 26. Dedicated ports, and refuse a busy port instead of drifting
Opening the UI at localhost:3000 showed Grafana: another program already held
port 3000, and Next.js silently moved to 3001. Defaults are now 3100 (UI) and
8100 (API), configurable in `.env`; `make web` and `make api` refuse to start
on a busy port and name the program holding it; `make doctor` reports both.

## 27. No known-vulnerable dependencies ship; audits run in CI
`npm install` reported 1 critical and 1 high advisory: Next.js 14.2.35 had 25
published advisories (including unauthenticated remote code execution in
image optimization) with no fix on the 14.x line, plus a vulnerable PostCSS
it bundled. Actual exposure was low (local development only, and none of the
affected features were used), but shipping known-critical CVEs is not
acceptable. Upgraded to Next.js 16.4.3 (Node 20.9+); the only code change was
reading page `searchParams` asynchronously. Verified: 0 npm vulnerabilities,
0 known Python vulnerabilities (pip-audit on requirements.lock), type check,
build, and all six pages served with real data. CI now fails on high or
critical advisories, Dependabot opens weekly update PRs, and `make doctor`
checks the Node version.

## 28. The project declares its Node version, and checks it first
After the Next.js 16 upgrade, `make web` on the Mac ran `npm install` under
Node 18.20.8 (end of life since April 2025) and then failed inside Next.js.
Root cause: the upgrade raised the Node requirement without checking the
machine it runs on, and `make web` never checked Node before installing.

Fix: `web/.nvmrc` pins Node 22 (the version the build was verified on);
`scripts/serve.sh` checks Node before `npm install` and, when nvm is
installed, switches to the pinned version automatically (installing it once);
otherwise it stops with the exact fix. CI reads the same `.nvmrc`, so CI,
the sandbox, and the Mac run the same Node. Tests simulate Node 18 with and
without nvm and assert that npm is never run on an unsupported Node.

## 29. Make never depends on a file's executable bit
`make web` failed with "Permission denied": an in-place edit (`sed -i`) made
during a test-of-the-test dropped `serve.sh`'s executable bit, and the commit
recorded the downgrade (100755 to 100644). The Makefile now runs every script
as `bash scripts/...`, so the bit no longer matters; a test asserts that, plus
that every script is still tracked as executable, and the commit step refuses
any 100755 to 100644 change.

## 30. Local servers listen on this machine only; React 19
`next dev` printed `Network: http://192.168.1.168:3100`: by default it listens
on every network interface, so anyone on the same Wi-Fi could open the app.
The UI now binds to 127.0.0.1 (verified: the only listening address is
127.0.0.1:3100), matching the API. A test guards both. Also upgraded React 18
to 19.3.1, which Next.js 16 deprecated and Next.js 17 removes; verified with
0 vulnerabilities, type check, build, and all six pages serving real data.

## 31. Aggregate at load time, not on every page view
With full M5 loaded, Planning took 7-8 s and Overview about 1.2 s on the Mac.
Measured cause (sandbox, 6.7M weekly rows): picking daily vs weekly scanned
the whole table (788 ms, run twice per Planning view), the top-series list
summed all rows (1,817 ms), and Overview counted all rows (228 ms). A
per-series summary (`sales_series_stats`, 30,490 rows) is now refreshed once
per load batch (1.2 s, concurrently, so readers are never blocked), and the
API reads it: 9.2 ms, 1.8 ms, and 4.1 ms; the endpoints respond in 9-13 ms.
Summary totals equal a live recount (6,719,161 rows, 66,927,173 units), and a
test asserts the summary always matches the raw rows.

## 32. Short payments go to a person, always
On a remittance, "paid part of invoice A" and "typed A but meant a smaller
invoice B" look identical. Posting short payments automatically produced
wrong postings in the first version. They now go to review with the
referenced invoice as the suggestion. Cost: 47 lines a person confirms.
Benefit: 0 wrong automatic postings in 875 (95% CI up to 0.4%), against 5 for
the baseline. Finance teams review short payments anyway, because a short
payment usually hides a deduction.

## 33. Never typo-correct a reference that exists
Invoice numbers are sequential, so a real number is one edit away from its
neighbours. Fuzzy matching now runs only when the reference names no invoice
at all. A reference to an already-paid invoice goes to review as a possible
duplicate payment.

## 34. Deduction verdicts come from records, in claim order
Every verdict cites records (promotion and retailer POS, receipt, shipment,
invoiced value) and the arithmetic, stored as an append-only assessment and
calculation. Claims are processed oldest first and each one uses up the
support it is granted, so a duplicate is found because the allowance is
already spent, not by comparing claim text.

## 35. POS proof of performance is its own table
Walmart weeks start on Saturday; the world's weeks are ISO weeks (Monday).
Putting ISO-week rows into `sales_observation` beside the M5 Walmart-week rows
would double count units in every weekly series, so retailer POS for
scan-back checks lives in `retailer_pos_week`.

## 36. Deadlines use the world's clock
The financial world ends on 2016-05-22. "Due in 14 days" measured from today
would always be zero, so the API measures deadlines from `world_clock`.

## 37. Evaluation is stored, not computed on page load
`make eval-recon` replays both engines from a clean ledger as the engine role,
scores them against `eval.*`, and stores the result in `eval.report` with the
code version and world. The UI reads the stored report, so opening a page can
never run an engine or recompute against the answer key.

## 38. One normaliser for answers and predictions
FATURA stores "Invoice Date: 04-May-2007" as the answer. Comparing raw strings
would mark "2007-05-04" wrong. Gold and every extractor's output go through
the same typed normaliser (dates, exact decimals, currency codes, ids), and
it refuses ambiguous input (03/04/2008) instead of guessing.

## 39. FATURA's official unseen split is used once
Its dev and test lists are the same 10 layouts. Tuning on dev and reporting
test would report a tuned score as unseen. They form one holdout scored once;
calibration uses 8 of the 40 training layouts, held out from fitting.

## 40. Layout answers never sit where the engine can read them
`source_document.layout_cluster` is readable by the engine role. The true
layout id is kept in `eval.doc_meta`, so the layout router is scored, not told.

## 41. Do not score checks a dataset cannot support
FATURA's totals do not add up (8 of 5,597) and its dates are random. The
arithmetic check is evaluated on data where totals are real, and FATURA's
results say so, instead of penalising correct reads.

## 42. The router escalates rather than guesses, and is built not to need to
A wrong layout means reading a new supplier's invoice with another layout's
field positions, which produces confident wrong values. Every router setting
is ranked first by misrouted new invoices. Router 1.0.0 was safe but
escalated 23% of scanned known invoices; 2.0.0 compares only each layout's
stable cells with a one-cell slide and recognises 99.4% of them, still with
0 misrouted out of 4,000 new held-out invoices.

## 43. Image libraries are locked dependencies
Pillow and NumPy were available in one environment by accident. They are now
declared and pinned; the lock script starts from the existing lock so adding
a package moves no other version.

## 44. Additions approved after researching Confido (October 2026)
Sources: Confido's Series B announcement (agent-driven workflows that surface
only decisions needing a person), its foodservice product (line-level claim
matching, double-dip detection, partial disputes with backups, mappings
learned once), and its Senior ML/AI job post (intermittent demand at scale,
rolling-origin backtests, best-fit selection, leakage and train/serve skew).
Added, without removing or reordering anything in the plan:
1. Reviewer actions in the UI, every action audited (Phase 2b, 3)
2. Dispute packet with attached evidence (Phase 3)
3. Double-dip detection across agreements (Phase 2a extension)
4. Mapping memory: one human fix applies forward and becomes a test (Phase 2b)
5. Upload an invoice and see each field, its box, and its confidence (Phase 2b)
6. Intermittent-demand forecasting with best-fit per series (Phase 5)
7. Train/serve skew and drift monitors (Phase 4)
8. A public demo running on recorded model calls (Phase 6)
And the known weaknesses are owned: router scan robustness (fixed in 2.0.0),
deduction checks earning their score from documents (Phase 3), short pays
auto-posted when a same-check deduction explains them (Phase 2a extension),
major dependency upgrades as a tested step.

## 45. Rules are learned, not typed in
The label vocabulary and field areas come from the fit layouts' training
answers by code. A hand-written label list would encode what the author saw
across all layouts, including the holdout, and silently inflate the
new-layout score.

## 46. Freeze, then score once
Rules were debugged on fit-layout dev invoices until the remaining errors
were OCR misreads, then frozen and scored once on the holdout. The 284 wrong
answers on new layouts are left for confidence and the model tiers rather
than patched, because patching them would turn the holdout into training data.

## 47. OCR output is versioned by engine
OCR results are cached and stored with the Tesseract version, page mode, and
scale. Two machines with different Tesseract versions produce two clearly
labelled results instead of one silently mixed one.

## 48. A model must show where it read a value
The local model read 96% of fields but invented totals and confused roles.
Every model value is grounded on the page by OCR, in its field's own labelled
line, or discarded.

## 49. Two readers must agree before a value skips review
Agreement between independent readers was right on every one of 1,529
fields; disagreement went the other way. The rule accepts agreement, asks a
third reader otherwise, and sends the rest to a person with all candidates.

## 50. Absence must be verified, and presence needs two witnesses
1.0 accepted "no reader found it" as proof of absence (10 wrong on
calibration). 1.1 requires that no label for the field is printed. Holdout
showed two more holes, both from depending on OCR alone: a line OCR dropped
and a field without a label. 1.2 blocks "absent" when two model readers read
the same value and asks the third reader for unlabelled fields.

## 51. Paid calls have a hard cap and a ledger
Every Claude call is checked against a dollar cap using its worst case
before it is sent, and its real cost is logged. The first live run sent an
outdated parameter; it cost nothing, and runs now stop after 3 identical
rejections and never report a failed third reader as a result.

## 52. A holdout is scored once per rule
Round-1 holdout results were seen while redesigning, so the redesigned rule
was scored on round 2. Rule 1.2, designed after seeing round 2, will be
scored on round 3.

## 53. Evidence for tests must be independent of the rules being tested
The first deduction checker read evidence generated by the same rules that
created the claims and scored 100%. Evidence is now damaged realistically
and independently; "needs evidence" is a verdict of its own, and the checker
never approved an invalid claim on the report region (0 of 39 under evidence 2.0).

## 54. Test damage is an overlay keyed by business keys, never an edit of the records
Evidence 1.0 altered the system of record and chose records by database id
order, so two machines with the same seed damaged different records and got
different scores. 2.0 hides or mis-keys records through an overlay the engine
reads as `observed.*`, decides each record by a hash of its business key, and
prints world and damage fingerprints so any two runs can be compared. A
repair command restored the altered records, verified against a rebuilt world.

## 55. Generated identifiers never come from database ids
The world generator named claims after a database id. Database ids depend on
what else was inserted before (even a failed attempt), so they are not part
of the data. Generator 1.1.0 numbers claims by their position in the world.

## 56. Audit the answer key before scoring against it
CORD's labels were checked for internal consistency before any arithmetic
rule is scored: 947 of 986 receipts add up (allowing printed rounding). Rules
that check arithmetic will be scored only on those, with the rest reported
separately, so label errors are never counted as model errors.

## 57. A duplicate image keeps one answer key; across splits it blocks the load
Two CORD training photos were published twice with different labels. Keeping
both would score one image against two answers; dropping the copy is
deterministic and reported. The same image in two splits would leak test
answers and stops the load.

## 58. On receipts, arithmetic is a witness
A reading may skip a person when its own numbers close (lines to subtotal,
subtotal to total, cash to change). Unlike a second model, arithmetic does
not share a model's blind spots. Claude is called only for readings that do
not add up. The rule is chosen on CORD validation and scored once on test.

## 59. Models copy, code classifies
Asked to fill named fields, the vision model invented values that are not
printed (a subtotal equal to the total on 33 of 100 receipts). Asked to copy
the printed lines, it has nothing to invent; a learned label vocabulary then
decides each line's field. The same split as everywhere else: models read,
deterministic code decides.

## 60. Never let a parser turn text into floats
A JSON number 89.100 is 89.1 to a parser and 89,100 rupiah on the receipt.
Model output is parsed with numbers kept as literal text, and amounts must
also be plausible for the currency, because arithmetic cannot detect a
consistent scale error.

## 61. An unexplained amount stops straight-through processing
A receipt reading may skip a person only if every amount printed in its
totals block has a known meaning. Unknown lines ("NET1 AMT", "ITEMS :1
41,000") were behind most of the wrong acceptances on validation.

## 62. A field that every document has can never be accepted as missing
Rule 1.3: an invoice always names its seller, so no reader finding one means
the reading failed. Scored on a fresh round (4), not on the round that
revealed it.

## 63. Additions 9-12, from Confido's current job posts (approved 2026-10-31)
Mapped to what the Applied AI/ML and Senior ML/AI posts ask for, and placed
in the existing phases so they do not displace the plan:
9. **Anomaly detection** on retailer POS and deductions (Phase 4), scored
   against the evidence overlay, whose injected restatements and gaps are
   labelled ground truth. ("Flag irregularities ... using anomaly detection.")
10. **Promotion recommendation** (Phase 5): lift measured on real M5 price
   drops, a ranked recommendation of which promotion to run where, with
   expected return and a backtest. ("Recommendation systems to improve
   retailer promotions"; "promotion optimization.")
11. **Risk-controlled straight-through** (Phase 4): choose the accept rule so
   the error rate is bounded with stated confidence on the calibration split
   (conformal risk control), instead of reading one point estimate.
   ("Guardrails for production"; "monitoring for systems without a single
   correct answer.")
12. **A small fine-tuned model** (Phase 3): LoRA on a small local model for
   deduction reason text to code, against the prompted model and the rules,
   on the same held-out claims. ("Fine-tuning LLMs" is a listed nice-to-have.)
Additions 1-8 already cover document extraction, agentic workflows with
eval harnesses, entity reconciliation (mapping memory), intermittent-demand
forecasting with rolling-origin backtests, and train/serve skew.

## 64. Two models agreeing is weaker evidence than a model agreeing with OCR
Round 4: both buyer-name errors were the local model and Claude agreeing
against OCR + rules, which had it right. Readers that fail differently are
worth more than readers that are each stronger but fail alike.

## 65. The weakest reader must not veto the strongest
Grounding treated OCR as the judge of what is printed. On layouts where OCR
reads worst, that let it overrule two models reading the same id, date, or
amount. Routing 1.4 accepts those, and only those, because the evidence split
cleanly (115 of 115 right) from the case that must stay refused (57 of 57
wrong).

## 66. Report straight-through in two parts
An invoice missing a required field must reach a person; counting that as a
failure hides the real reading failures. Reports give straight-through on
complete invoices and the share of incomplete invoices sent to a person.

## 67. Recognise a repeated failure by its kind, and stop at once when no retry can help
Round 5: the API credit ran out at call 71 and the run made 229 more failing
calls. The "stop after 3 identical rejections" check compared full error
text, and every Anthropic error carries a unique request id, so no two ever
matched (the test used a fake error without one, which is why it passed).
Errors now carry a signature (status, type, message) and a fatal flag
(exhausted credit, invalid or unauthorised key: stop at the first call); one
shared FailFast guards both the invoice and receipt runs. Nothing was lost:
the 70 answers before the failure are recorded and replay for free, and the
report refused to present an incomplete third-reader run as a result.

## 68. Human finance decisions belong to the reviewer role only
Migration 0006 let the engine change deduction and cash-match statuses, though
no engine code did: the grant only meant the engine could record a decision a
person is supposed to make. 0017 revokes it. Approving a match posts the cash
and marks the invoice paid, exactly as automatic posting does, and every
decision is audited with the engine's recommendation and whether the person
agreed, so overruling the engine is visible rather than silent.

## 69. Wait for a ready database, not an open port
A Postgres that is still starting refuses connections, so tests and
`make migrate` run right after a start failed (114 errors on 2026-11-04).
`wait_until_ready` retries a real query to a deadline and then fails once
with what to do. Separately, the sandbox test harness trusted a stale
postmaster.pid whose process id had been reused by another process; it now
checks that the process really is Postgres.

## 70. Every confirmed value is a test, and a regression differs from an old mistake
The rule's inputs are stored with each decision (0018), so any later rule can
be replayed on exactly what it saw, with no model calls. The latest human
review of each field is its truth. Replayed, a field is kept right, sent to a
person (slower, never wrong), an open mistake (the rule still accepts the very
value a person corrected: a known bug, counted but not blocking), or a
regression (it now accepts a wrong value it did not accept before: the gate
fails). Blocking on old mistakes would keep the gate red until all are fixed,
and a gate that is always red gets ignored. FATURA cases are exported to
tests/fixtures so CI replays them without a database; other document sets are
never exported.

## 71. Post a short-pay only when the line explains it; name a double-dip by the claim it repeats
Remittances short-pay in two ways. A KeHE-style line can carry the invoice
total, the deduction, and the net ("gross 1,000, shortage 50, net 950"); the
engine used to skip such lines entirely, so their cash was never applied. Now,
when the reference is exact and the line's total equals the invoice's open
balance to the cent, the net is posted automatically and the deduction is
validated as its own claim (cashapp 1.1). A bare partial payment with nothing
explaining it still goes to a person: it looks the same as a remittance that
names the wrong invoice. The deduction engine already rejected a repeated claim
by arithmetic (each claim uses up its support); it now labels it a duplicate
and links the earlier claims (deductions 2.1), which is the sentence a dispute
letter needs. World generator 1.2 models both: retailers hold invoice claims
to net them against that invoice's payment, and sometimes claim them again
later. With both settings at 0 it reproduces the 1.1 data exactly (checked
table by table against the previous generator); the fingerprint changes
because it includes the generator's source. One definition of a cash line
(CASH_LINE_SQL) replaces five copies.

## 72. An exact reference is not enough when another open invoice has the same amount
Found while scoring world 1.2: two stores ordered the same goods, so two open
invoices were both $603.60, and a remittance quoting the wrong one was posted
at 0.99 (exact reference, exact amount). World 1.1 had simply never drawn
this coincidence, so its zero false matches was partly luck. When another open
invoice of the same customer in the last 90 days has the identical amount,
the match is now suggested at 0.85 for a person to confirm. A wrong posting
marks the wrong invoice paid and sends collections after a customer who
paid; the coverage cost is visible in the precision-coverage curve, and must
be read on the full world (the 3-product test world exaggerates twins).

## 73. Names stay with a person: rule 1.5 rejected on evidence
`make names-analysis` (development rounds 1 to 4, 910 invoices, 1,820 name
fields, no model calls) measured accepting a name the local model and Claude
read alike when OCR does not confirm it: 732 such fields, 728 right (99.45%,
95% CI 98.60-99.79%). The bar is the lower bound rule 1.4 holds on accepted
values in round 5, 99.72%; the candidate's lower bound is below it, so it would
lower accuracy and is not proposed. The four misses were not misspellings:
both models read a different person ("Martha Elliott" for "Jason Holland").
These invoices print two parties, buyer and bill-to, and the models took the
wrong one. Requiring the name to be found on the page filtered nothing, since
the wrong name is printed there too. Role confusion, not reading, is the
failure; it needs the field's position or its printed label, not a second vote.

## 74. A rejected repeat names the first claim even when that claim used nothing up
On the full 1.2 world all 4 duplicate claims were rejected but only 3 named
the claim they repeated. The fourth repeated a bogus claim (a shortage the
receipt does not show): the first was rejected too, used up no support, and so
left nothing to link. The engine now remembers every earlier claim against
the same thing (receipt line, invoice late fee, or promotion store, product
and period) whatever its verdict, and any rejected claim lists them. The
verdict still comes from the evidence; the link only strengthens the dispute.
Both regression tests fail on the previous engine and pass on this one.

## 75. The agent is judged on the evidence a supplier really has
Before building the agent, its role was checked: it could still read the
undamaged originals under the evidence overlay, each claim's true store, the
damage report, and the M5 units the retailer POS is derived from. An agent on
that role would have scored well by reading the answer. 0019 and 0020 give it
the engine's view plus weekly shelf prices, which a supplier really sees;
tests prove each hidden table is unreadable (7 fail without 0019).

## 76. A price drop may stop a dispute, never start an approval
When no agreement is on file, a promotion-level shelf price in the claimed
weeks means one probably ran: the agent asks for it instead of disputing, since
a lost dispute costs a relationship and analyst time. The same signal is never
enough to accept, because a price drop does not say what allowance was agreed.

## 77. Design-region review of the agent: what it added, and the one fix before scoring
California (142 claims): 0 false approvals of 87 and 0 false disputes of 41,
the same as the engine; the agent's only difference was turning the engine's
14 undecided claims into named document requests (11 of those claims were
valid, so asking rather than disputing was right). The price playbooks never
fired: California has no claim where a missing agreement or shifted end date
misled the engine, so they meet real data first on the scoring region, as
designed from business rules rather than tuned. One weakness: a claim with no
store where 10 stores ran the promotion was sent back for the store. 1.1 keeps
the promoting stores whose POS (as provided) shows exactly the claimed units,
since a scan-back claims the units one store sold; a unique match is the store.

## 78. Investigator 1.1, scored once on Texas and Wisconsin (report 28)
0 false disputes of 38 (the engine alone: 2 of 41), 0 false approvals of 119,
38 of 38 disputes exact to the cent. Both engine false disputes were damaged
records the price playbooks were written for: R-0000268 (promotion missing
from the calendar) and R-0000357 (promotion end keyed a week early); both
became document requests. The cost: R-0000295, a real over-claim whose end
date was also shifted, was asked about rather than disputed. 13 requests in
all, 12 for claims that were valid. Not tuned on these regions: the price
playbooks never fired on California and were written from business rules.

## 79. A letter may only contain numbers the case can trace
Every amount, unit count, and percentage in a dispute or request letter must
appear in the agent's facts or a calculation the case cites; dates and record
ids are checked as ids, not amounts. A template letter is always available at
no cost; a model-written letter is used only if it passes the same check,
otherwise the template is used and the invented numbers are shown (tested
with a model that adds $512.50). On the full data, all 312 letters pass and
all 79 dispute letters state the fact from the records ("shows 24 of 24
units received") rather than a rule name.

## 80. The case screen tells the truth about the dispute window
249 of 312 claims are past their dispute deadline at the ledger date. The
agent's analysis is unchanged (the evidence says what it says), but the case
screen marks each window, sorts claims that can still be recovered first, and
letters never ask for anything by a deadline that has passed. Found by reading
a real case on screen. Also found there: plain dict endpoints let FastAPI turn
Decimal money into floats, which crashed the screen; case data keeps money as
exact strings, with a test that fails on a float.

## 81. The local model may write letters; the number check decides if they are used
`make letters-local` asks the local model (Ollama, no per-call cost) for each
open dispute and request letter, checks every number in the draft against
the case, and records the draft and the check as an agent audit event (model
and prompt versions included; answers recorded, so a re-run costs nothing).
The case screen shows the model's draft only while it still passes, and
otherwise says what it invented and shows the template. The share of drafts
that pass is itself a result: how often this model invents a figure when told
not to.

## 82. Receipt labels: the most reliable label wins, not the longest
"Cash Payment" was read as a card payment (CORD 0016) because PAYMENT is
longer than CASH. Labels now carry a smoothed share of their train lines that
are their field. Every CORD label line that changed is now right and none got
worse; validation is unchanged, so this fixes a known mistake rather than
raising a score.

## 83. The number check reads dates too, and the first local-model run measured it
Local model (qwen2.5-VL 7B) on 20 letters: 18 passed, 2 rejected. Both
rejections were the checker's fault: correct dates written in words
("September 26, 2016") read as the stray numbers 26 and 2016. Worse, 1.2
skipped YYYY-MM-DD dates entirely, so an invented deadline would have passed.
Letter 1.3 reads dates in any common form and checks each against the case;
with it, all 20 drafts pass. Reading the passing drafts also showed engine
wording ("the engine could not decide") reaching the retailer: request
letters now give a plain reason, prompt 1.1, and a draft from an older prompt
is never shown.

## 84. Passing the number check is not the same as being right
The full local-model run (prompt 1.1) passed the number and date check on 105
of 105 letters, which looked like a perfect result. Reading three of them found
one asking for a document "by the dispute deadline of 2015-05-06" a year after
it closed (R-0000268). Applying a deadline rule to all 105 found 12 such
drafts (11%): numbers right, meaning wrong. The honest result for prompt 1.1 is
93 of 105 letters correct on every checked property. Letter 1.4 adds the rule
(a closed window may not be asked to act by its date; an open one may not be
called passed), rejects such drafts in favour of the template, and prompt 1.2
tells the model so and not to claim more than the evidence shows ("dropped like
a promotion" is not "part of a promotion").

## 85. Prompt 1.2 result: deadline mistakes 12 to 1, and the 1 is caught
Full run, 105 letters: 104 pass both checks; R-0000591 asked for a document by
a deadline that had passed, was rejected, and the template is shown. A read
of a sample found no engine wording and no "part of a promotion" overclaim;
some letters echo the instruction ("we will not be asking for anything by that
date"), true but stilted, left as a style note rather than tuned further on
the same 105 letters.

## 86. The model copies the deadline sentence; it never words it
Prompt 1.2 told the model a rule about the deadline, and some letters
repeated the rule to the retailer ("we will not be asking for anything by
that date"). The cure is to remove the choice: template and model letters now
share one deadline sentence, and prompt 1.3 gives it to the model to copy. A
third check rejects wording that echoes the instructions (field names, "as
instructed", "we will not be asking"). All 312 template letters pass all three
checks.

## 87. Addition 12 is scored on phrasings no reader has seen
Free-text deduction reasons decide which check runs; KeHE's IA code alone covers
shortages, late fees, and price differences. The reason text is generated from
industry vocabulary with abbreviations, keying errors, and remittance noise,
and each of 10 categories has phrase families held out entirely ("OTIF FINE"
for a late fee, "SWELL ALLOWANCE" for spoils), so a reader is judged on wording
it never saw. Baselines first: keyword rules reach 27% on unseen phrasings and
a character n-gram Naive Bayes 51% (100% on seen ones), because no letters link
"IRC" to "COUPON". A language model has to earn its place on that gap: the
local model prompted with plain definitions (a test proves no held-out word is
in the prompt; it caught "fine", "credit", and "invoice" in the first draft),
and a LoRA fine-tune of Qwen2.5-0.5B with settings fixed before any score.
Each reader is scored on held-out once per version. This is generated text,
not real remittances; the held-out families are what keep it honest.

## 88. First reader scores, and why the LoRA run crawled
Scored once each on 800 unseen phrasings: keyword rules 26.5%, character
n-gram Naive Bayes 50.5% (100% on seen phrasings), the local 7B model with
definitions only 70.9% (90.5% on seen). The language model knows "IRC" is a
coupon; the n-gram model only knows letters it has seen. Neither wins
everywhere, which is what the fine-tune is for. Letters with prompt 1.3: 105 of
105 pass all three checks. The first LoRA run took 347 s for one step: the 7B
model was still loaded in the same memory and every example carried all ten
definitions (about 200 tokens). lora-1.1, before any LoRA score: Ollama is
unloaded first, a short prompt with category names only, batch 8, progress
and time left on screen, and a stop with a plain message if steps stay slow.

## 89. The LoRA fine-tune does not ship, and the next design is scored on a fresh set
LoRA on Qwen2.5-0.5B: 99.8% on seen phrasings, 45.9% on unseen (95% CI 42.4
to 49.3%), below Naive Bayes (50.5%) and far below the prompted 7B model
(70.9%). Training loss was near zero by step 100: it memorised surface forms,
and a 0.5B model does not carry the domain knowledge the task needs. Having
read the first unseen set, designing on it would grade a design on its own
exam, so it becomes the development set and a second unseen set (30 new
phrase families, reasons-1.1.0) is committed before any reader is scored on
it; a test proves the first three splits are unchanged and the new families
are disjoint. The leak test, extended to the new set, caught five words from
the category definitions and one category name in the first draft of it; those
phrasings were reworded before anything was scored. Candidate next: a hybrid,
Naive Bayes when its per-n-gram margin is high (seen phrasings never score
below 0.6), else the 7B model.

## 90. The hybrid reader is the one to use
Scored once on unseen set 2 (800 phrasings nothing was designed on): keyword
rules 27.6%, Naive Bayes 62.6%, the 7B model 82.8%, the hybrid 85.2% (95% CI
82.6 to 87.5%), and 100% on seen phrasings against the 7B model's 90.5%. On
unseen wording the hybrid's interval overlaps the 7B model's, so the claim is
"at least as good, and perfect on familiar wording with no model call", not a
proven gain. The threshold (a per-n-gram margin of 0.484) was chosen on set 1,
where the model was asked on 525 of 800. Per-example predictions are not yet
stored in reports, so a paired test was not possible; future reports should
store them.

## 91. Settle the thin results with more fresh worlds and paired tests
The agent's headline (0 false disputes against the engine's 2) rested on one
world. make agent-seeds runs the frozen agent 1.1 and the engine on several new
worlds (world seeds from 101, evidence seeds from 2001; none is the main
world's), records every claim with both actions and the truth, and compares
them on the same claims with an exact McNemar test. M5 is loaded once into a
base database and each world is a copy, dropped after scoring. make
codes-paired does the same for the reason readers on unseen set 2: no new
score, only the frozen readers' replayed answers compared pairwise.
