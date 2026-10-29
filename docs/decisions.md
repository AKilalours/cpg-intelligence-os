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
