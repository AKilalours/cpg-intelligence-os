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
