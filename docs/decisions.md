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
never approved an invalid claim on the report region (0 of 35).
