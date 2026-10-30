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

## Results (report #1, world as of 2016-05-22)
Reproduce with `make finance-world && make reconcile && make eval-recon`. The
world: 60 real M5 FOODS items at 10 stores over 104 weeks; 1,040 invoices
(50,834 lines), 111 promotions taken from real price drops, 100 checks, 984
cash lines, 312 deductions (83 invalid). Every rate below is "k of n" with a
95% Wilson interval, because the bad categories are small.

### Cash application (automatic posting at confidence 0.90 or higher)
| | This engine | Exact-reference baseline |
|---|---|---|
| Wrong among automatic postings | **0 of 875** (0.0% to 0.4%) | 5 of 876 (0.2% to 1.3%) |
| Posted automatically | 875 of 984 (88.9%) | 876 of 984 (89.0%) |
| Dollars posted correctly with no person | **90.2%** | 88.4% |
| Sent to a person, with a suggestion | 109 (suggestion right 101 of 109) | 0 |
| Left with nothing | **0** | 108 |

By kind of remittance mess: clean 827/827 posted correctly; typos 48/50 fixed
automatically (2 to review); wrong references 22/22 to review (baseline posted
4 of them to the wrong invoice); missing references 38/38 to review with the
right suggestion; short payments 47/47 to review.

Two design rules carry the zero: a short payment is never posted automatically
(on a remittance it looks exactly like a wrong reference to a larger invoice),
and a reference to an invoice that is already fully paid is never "typo
corrected" to its neighbour, because invoice numbers are sequential and one
edit away from each other. Both were real wrong postings in the first version
and both have regression tests.

Residual risk, stated plainly: a wrong reference whose amount happens to equal
the referenced invoice's open balance cannot be detected from the remittance
alone. It happened 0 times in 984 real-anchored lines, because invoices with
dozens of lines rarely share a total. The 3-item test fixture shared totals
often, which is how the risk was found.

### Deduction validation
| | Result |
|---|---|
| Recoverable amount exact to the cent | 312 of 312 |
| Invalid claims accepted (false approval) | 0 of 83 (0.0% to 4.4%) |
| Valid claims disputed (false dispute) | 0 of 229 (0.0% to 1.7%) |
| Recoverable dollars in the answer key | $4,044.46, all found |

**How to read the 100%.** The validator checks claims against the same
published terms the world was generated from (allowance times retailer POS
units, the receipt, the shipment date, the 1% spoils allowance). A perfect
score therefore proves the evidence lookups, the "already used" bookkeeping
that catches duplicates, and the cent-level arithmetic. It does not prove that
messy real claims are solved: in reality, backup is a PDF, the store or period
is missing, and terms are negotiated per account. That is the job of Phase 2b
(document extraction) and Phase 3 (the investigating agent), which will be
scored on claims where this rule engine returns `needs_evidence`.

### Recovery with limited review time
Every policy uses the same validator; only the order of work changes. Disputes
below $25 are skipped (analyst cost, an assumption), claims within 21 days of
their deadline jump the queue, and a disputed claim is assumed to recover its
true recoverable amount, the same for every policy.

| Reviews a week | Engine order | Largest first | Oldest first |
|---|---|---|---|
| 1 | **$3,429.77** (98%) | $2,542.13 (73%) | $214.14 (6%) |
| 2 | **$3,496.71** (100%) | $3,220.43 (92%) | $1,036.90 (30%) |
| 3 | $3,496.71 (100%) | $3,496.71 (100%) | $2,731.07 (78%) |

The ranking matters only when review time is scarce, which is the realistic
case. At one review a week, largest-first lets $423.68 expire and oldest-first
lets $2,105.47 expire; the engine order loses nothing to deadlines.

The dollar amounts are small because the brand is small (60 items, 10 stores);
the shares are the transferable result.

### Generator bug found while building this
The first generator carried a week's deductions forward all-or-nothing when
they exceeded that week's checks. On small checks the backlog snowballed and
67 of 86 deductions in the test world were never written. Deductions are now
taken oldest first while the check stays positive, the rest wait, and the
report counts `deductions_pending`; a test asserts generated = written +
pending. The real-scale world was not affected (313 generated, 312 written,
1 pending at the as-of date).

## Fix: the deduction checker now earns its score (checker 2.0)

The first checker scored 312 of 312 because it read perfect evidence made by
the same rules that made the claims, like grading with the answer key the
student learned from. `make finance-evidence` now damages only the evidence
the checker can see, once, with its own seed (invoices, payments, cash
application, and the answer key are unchanged), and logs every change in
`eval.gt_evidence`:

| Damage | Assumed rate | Count in the world |
|---|---|---|
| Retailer POS week missing | 3% | 1,868 of 62,400 weeks |
| Retailer POS week restated by 1-3% | 5% | 2,456 weeks |
| Receipt line never scanned | 4% | 2,089 of 50,834 lines |
| Delivery date not keyed | 3% | 31 of 1,040 shipments |
| Promotion missing from the calendar | 2% | 1 of 111 |
| Promotion end date keyed a week off | 4% | 8 |
| Claim arrives without its store | 3% | 21 of 312 claims |

Checker 2.0: a missing POS week, receipt, delivery date, or store gives
"needs evidence" instead of a verdict; a scan-back claim within 3% of what
the POS supports is accepted (restatements are at most 3%; over-claims are
10-40%). A first version had a $1.00 minimum tolerance, which approved four
over-claims of 20-100% on claims under $4; it is now 5 cents (rounding only).
Settings were chosen on California stores and reported on Texas and Wisconsin.

| Region | Claims | Needs evidence | Invalid claims approved | Valid claims disputed | Exact when decided |
|---|---|---|---|---|---|
| California (design) | 142 | 10 | 0 of 41 | 0 of 91 | 131 of 132 |
| Texas + Wisconsin (report) | 170 | 15 | **0 of 35** | **4 of 120** | 151 of 155 |

The four wrongly disputed claims are exactly the damage data alone cannot
reveal: three promotions whose end date was keyed a week early, and one
promotion missing from the calendar. Reading the signed promotion agreements
is the Phase 3 agent's job.
