# Phase 2b: Document Intelligence Engine

Goal: read invoices into validated fields, escalating from cheap to expensive
only when needed (rules and OCR, then a local model through Ollama, then
Claude Sonnet 5.5), with a confidence that is measured before it is trusted.

## Step 1: the answer key (done)

Analogy: before grading an exam you print one answer sheet, and every student
is graded against that same sheet in the same way. Here the "students" are the
rules tier, the local model, and Claude.

**Data.** FATURA, Zenodo record 10371464 (CC BY 4.0), `FATURA2.zip`, MD5
`4c9404462f22c5241eb1a290a02eb2a2` as published, SHA-256 pinned in
`data/datasets.lock.json`. 10,000 invoice images, 50 layouts x 200. Origin:
`real_template_generated`, reported separately from real documents (DocILE).

**What was checked on all 10,000 invoices, and what it means**

| Finding | Consequence |
|---|---|
| Boxes are PDF points from the bottom-left of a 595 x 841 page | Flipped and scaled to [0, 1] from the top-left; verified by drawing them on an invoice |
| Values include their printed label ("Invoice Date: 04-May-2007") | Gold values are typed by `cpgos/documents/normalize.py`, the same code that types every extractor's output |
| Line items are printed but not labelled | FATURA scores header fields only; line items wait for DocILE |
| subtotal - discount + tax = total on 8 of 5,597 invoices | Totals are random: arithmetic checks are not scored on FATURA |
| Due dates before invoice dates are common | FATURA scores reading a date, not judging it |
| "Total in words" equals the numeric total on 3,600 of 3,600 | A valid cross-check signal on the 20 layouts that print it |
| Official unseen-layout split lists the same 10 layouts as dev and test | Merged into one `holdout`, scored once, never tuned on |
| Holdout layouts have no PO number or amount due | Those two fields are measured on familiar layouts only, and labelled so |

**Splits** (stored per document in `eval.doc_meta`)

| Split | Layouts | Invoices | Use |
|---|---|---|---|
| fit | 32 | 6,400 | Build rules, layout signatures, prompts |
| calibration | 8 (seed 2026) | 1,600 | Fit confidence thresholds on layouts the builders never saw |
| holdout | 10 (official) | 2,000 | Final score, once |

The familiar-layout split (`strat1`, 7,500 / 1,250 / 1,250) is kept too, for
reporting seen-layout accuracy beside unseen-layout accuracy.

**Coverage.** 76,194 gold values over 13 fields; 0 values the normaliser
could not parse. Ambiguous inputs (03/04/2008) return no value rather than a
guess, and that is tested.

**Leakage.** Gold values, true layout ids, and splits live in `eval.*`, which
the engine role cannot read (tested). The engine-readable
`source_document.layout_cluster` stays empty, so the layout router cannot be
handed its own answers.

Reproduce: `make download DATASET=fatura && make load-fatura` (about 8 s;
re-running changes nothing).

## Step 2: layout routing (done, router 2.0.0)

Analogy: a person recognises a printed form by its frame (logo, boxes,
printed labels), not by what was written into it, and still recognises it
when the scan is a little crooked.

**How it works.** Each page becomes a 48 x 68 grid of ink darkness. For each
known layout the router keeps the cells that barely change between its
invoices (its frame, and the blank space around it) and compares only those,
allowing the page to slide one cell in any direction. Training also uses a
scan-damaged copy of every invoice, and each layout gets its own threshold.
Routing runs as the engine role and reads only the image.

**How settings were chosen.** 12 combinations were scored on fit-layout dev
(known) and the 8 calibration layouts (new), ranked by: fewest new invoices
routed to a known layout, then most scanned known invoices recognised, then
the widest safety margin. Winner: scan-like training, stable cells only,
one-cell slide: 0 of 3,200 calibration invoices misrouted, 800 of 800
scanned dev invoices recognised, closest new invoice 46% beyond its threshold
(runner-up: 6%).

**Scored on held-out invoices** (`make eval-router`)

| Test | Router 1.0.0 | Router 2.0.0 |
|---|---|---|
| Known layouts, clean (800) | 796 | **800** |
| Known layouts, scan-like (800) | 618 (77%) | **795 (99.4%)** |
| Known invoices sent to the wrong layout | 0 | **0** |
| New layouts called new, clean + scan-like (4,000) | 4,000 | **4,000** |
| Closest new invoice, as a multiple of its threshold | | **1.74** |

Scan-like = rotation up to 2 degrees, shift up to 12 px, blur, JPEG quality
35 to 70. Routing all 10,000 documents puts exactly the 6,400 fit-layout
invoices in known lanes and exactly the 3,600 calibration and holdout
invoices in "new".

**Experiments that were measured and rejected** (dev and calibration only)

| Variant | Scanned known recognised | New invoices misrouted |
|---|---|---|
| 1.0.0 whole page | 621 / 800 | 11 / 1,600 |
| Crop to printed content | 779 / 800 | 482 / 1,600 |
| Whole page + slide | 757 to 792 / 800 | 70 to 454 / 1,600 |
| Stable cells, no slide | 799 to 800 / 800 | 7 to 3,200 / 3,200 |

The lesson: sliding alone lets different frames look alike because the
changing words dominate; ignoring the changing words alone is fragile to
shifts; together they separate cleanly.

**Reading these numbers honestly.**
- FATURA's layouts are clean templates. Scan-like damage here is simulated,
  so DocILE (real scans) is the real test.
- The familiar-layout test invoices were scored for 1.0.0 too, so the 2.0.0
  numbers on them are a second look. Every 2.0.0 setting was chosen without
  them, and the holdout layouts were never read during training.
- During the first exploration a script printed holdout AUROC; it changed no
  choice. Choices now happen only inside `layout.train`.

Reproduce: `make train-router && make eval-router && make route-docs`
(about 2.5 minutes in total).
