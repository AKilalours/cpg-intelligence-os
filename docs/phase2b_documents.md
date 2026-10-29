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
