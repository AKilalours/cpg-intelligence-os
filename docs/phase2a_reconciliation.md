# Phase 2a: Financial reconciliation on real-anchored data

## Why this data design
No public dataset links real remittances and deductions to real invoices
(companies never publish them). So the financial events are generated, but
everything they are anchored to is real:

| Element | Source | Real or generated |
|---|---|---|
| What was shipped and sold | M5 weekly units per store and item | Real |
| Prices | M5 weekly shelf prices | Real |
| Promotions | Real M5 price drops (TPRs) | Real events; agreement terms assumed |
| Deduction codes | KeHE published prefixes (WP, IA, WG, ...) | Real |
| Fees and dispute window | KeHE published: $250 late-shipment fee, ~180-day dispute window | Real |
| Invoices, payments, remittances, deductions | Generated from the rows above | Generated, labeled `real_anchored_generated` |
| Noise (typos, partial pays, overclaims) | Generated at stated rates | Generated |

A concrete example, with real M5 figures: in the week of 2014-11-24,
FOODS_3_090 at store CA_2 sold 315 units at $1.25, against a regular price of
$1.48 over the previous 8 weeks (both real). The brand's invoice for that
store and week ships 324 units (27 cases of 12). The real $0.23 price drop
makes a scan-back promotion; at the assumed 60% allowance that is $0.14 per
unit (0.138 rounded to the cent), so a WP deduction of 315 x $0.14 = $44.10
is valid. A WP deduction claiming 400 units is invalid for 85 x $0.14 = $11.90.

Weeks are ISO weeks (Monday start), computed from M5's daily rows.

## The fictional brand
"Northfield Pantry Co." owns a fixed set of M5 FOODS items (anonymized in M5,
so no real brand is implied). It sells through one distributor, KeHE, which
ships to the 10 M5 stores. The last 104 weeks of M5 are used.

## Assumptions (each one is a parameter in the generator config)
| Assumption | Value | Why |
|---|---|---|
| Wholesale price | 62% of the quarter's median shelf price, to the cent | Typical CPG distributor/retail margin stack; stated, not measured |
| Case pack | 12 units; ship = next week's real units rounded up to cases | Shipments lead demand |
| Payment terms | Net 30, actual payment day jitter of plus or minus 5 days | Common distributor terms |
| Promotion allowance | 60% of the real price drop, per unit sold, scan-back | Brand funds part of the TPR |
| Spoils allowance | 1.0% of invoice value per quarter | Common negotiated allowance |
| Shortages | 8% of deliveries have one line short by one case; 3% of fully received deliveries get a false shortage claim | Shortages are drawn per delivery, not per line (a per-line rate made 64% of invoices short in the first run) |
| Spoils claims | 0.5% to 1.6% of quarterly invoice value against a 1.0% allowance | About half exceed the allowance |
| Remittance noise | 6% typo in invoice ref, 3% missing ref, 2% wrong ref, 5% partial pay | Stated rates, varied in sensitivity runs |
| Deduction validity mix | Overclaims, duplicates, unsupported promos, unproven shortages, on-time "late" fines | Each tagged with a ground-truth reason |

## Ground truth and leakage prevention
The answer key lives in a separate `eval` schema. Matching and validation run
as the database role `cpgos_engine`, which has no access to `eval` at all, so
an engine cannot read the labels even by accident. Scoring runs afterwards
with a role that can.

## What is measured
- Cash application: auto-applied precision (and its complement, the false-match
  rate), share auto-applied, share sent to review, share left unmatched, and
  dollars allocated correctly. Compared with an exact-reference-only baseline.
- Deductions: false approval rate (accepting an invalid deduction), false
  dispute rate (disputing a valid one), recoverable dollars found, and dollars
  recovered in the top-k of the priority queue versus sorting by amount.
