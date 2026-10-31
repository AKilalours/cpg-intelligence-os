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

## Step 3: tier 1, OCR plus rules (done)

Analogy: a clerk who has handled a supplier's invoices for months knows where
the total sits (known-layout path). Given an unfamiliar invoice, the clerk
looks for printed labels like "TOTAL" and reads what follows (label search),
and leaves a field blank rather than guess. Blanks go to the model tiers.

**What the rules know, and where it came from.** Everything is learned by
code from the 32 fit layouts' training answers: the page area of each field
per known layout, and the printed labels that precede each field. Nothing is
a hand-written list, so the label search uses no knowledge of the holdout.

**OCR.** Tesseract, pages enlarged 2x (values read exactly: 87.3% at 1x,
98.4% at 2x, 97.9% at 3x, on 40 training invoices), one thread per Tesseract
process, cached per image and engine version.

**Bugs found on fit-layout dev invoices, each with a regression test**

| Symptom | Root cause | Fix |
|---|---|---|
| 40-page OCR batch did not finish in 3 minutes | Each Tesseract used every core; 4 in parallel oversubscribed the CPU | One thread per process |
| Buyer names and PO numbers read as ":Shelia Swanson", ":54" | Separator after the label kept | Strip separators |
| Invoice date read from the "Due Date" line; due dates never found | FATURA's own key labels "Due Date" as the invoice date on 1 layout, so the label belonged to two fields | A label belongs to the field that uses it on the most layouts |
| "Note:Total payment due in 14 days" read as a total | Label matched mid-sentence | The value must start right after the label |
| "GSTIN : 24HDE..." read as tax | "GST" matched inside "GSTIN" | Whole-word labels |
| Company names learned as "labels" | Free text treated as a label | A label must be followed by a value |
| "BILL_TO: INVOICE ID INV57..." as a buyer name | OCR merged two columns into one line | A value stops where the next label begins |
| "eighty-" alone parsed as 80; continuations missed | OCR lists lines across columns, so the next line in the list belonged to the other column | Continuation = nearest line below at the same left edge; a trailing hyphen always continues |

On 120 dev invoices, label search went from 150 wrong to 3 wrong; the 3 are
OCR misreads. Rules were then frozen.

**Scored once** (`make eval-extract`; Tesseract 4.1.1 in the sandbox, so the
Mac run with Tesseract 5 is the official one and may differ slightly)

| | Known layouts (800 invoices) | New layouts, holdout (2,000) |
|---|---|---|
| Correct | 5,992 | 14,439 |
| Wrong | 47 | 284 |
| Abstained (left for the models) | 10 | 2,476 |
| Spurious | 1 | 1 |
| Precision: when it answers, it is right | **99.2%** | **98.1%** |
| Coverage: fields read correctly | **99.1%** | **84.0%** |

On new layouts, 1,800 of the 2,476 abstentions are seller names, which have
no printed label; without them coverage is about 94%. The 284 wrong answers
on never-seen layouts (1.9%) are the risk the next steps must catch:
calibrated confidence decides which answers may skip review, and the model
tiers re-read the rest. The holdout errors were not used to change any rule.

## Steps 4 to 6: the cascade, Claude as a third reader, and the two-readers rule

Analogy: a bank's four-eyes rule. A payment leaves without a manager only
when two people keyed the same amount independently. Here three readers fail
in different ways: OCR + rules misreads characters, the local vision model
(qwen2.5vl:7b on the Mac) confuses roles and sometimes invents values, and
Claude Sonnet 5.5 is asked only when the first two have not settled a field.

**Grounding.** A model value counts only if OCR finds it on the page, on a
line carrying the field's own label (an OCR slip of about one character is
tolerated), it is not a sum, and a name looks like a name. Designed on dev
and calibration invoices after the local model, ungrounded, answered 29
fields wrong or absent from the invoice on 20 dev invoices.

**Measured facts that set the rule** (first cascade run, report 8): when OCR
+ rules and the local model agree they were right on 1,067 of 1,067 holdout
and 462 of 462 calibration fields; when they disagreed, OCR + rules was right
3 of 19 times. So agreement is accepted, and disagreement goes to the third
reader, never to tier 1 by default.

**Official one-time score** (rule 1.1, frozen before scoring; 150 fresh
invoices from the 10 never-seen layouts, round 2, disjoint from round 1 which
was seen during design; report 11; $0.61 of Claude)

| | Two readers only | With Claude as third reader |
|---|---|---|
| Invoices straight through (no person) | 8% | **55.3%** (83 of 150) |
| Fields a person checks, per invoice | 1.61 | **0.69** |
| Accepted values correct | 98.4% | **98.5%** (1,819 of 1,846) |
| ... values filled in automatically | | **1,219 of 1,219** (95% CI >= 99.7%) |
| ... fields accepted as absent | | 600 of 627 |

Every value the system filled in was right. All 27 mistakes are fields
accepted as absent that were printed: the seller name on one layout and the
tax on another. Calibration had shown 100% with rule 1.1, so this is the gap
between design data and never-seen layouts, reported as measured.

**Diagnosed after scoring (numbers above unchanged):**
- Layout 12, tax: OCR dropped the whole tax line. The local model and Claude
  both read 23.39 correctly, grounding (which depends on OCR) discarded both,
  and the rule accepted "no tax".
- Layout 10, seller: the name has no label, so absence cannot be checked on
  the page, and Claude was never asked.

**Rule 1.2** (built and tested; calibration report 12 plus a replay; not yet
validated on fresh holdout): a value two model readers read alike that OCR
could not find anywhere on the page is never accepted as absent; fields
without labels always get the third reader before "absent" is accepted.

A first version counted any agreeing, unconfirmed reading. On calibration
it held back 20 invoices whose answer key has no buyer (both models read a
ship-to name that OCR found, in the wrong role), dropping straight-through
from 55.6% to 43.1%. Restricting it to values OCR could not find at all
restored 55.6%, with 1,986 of 1,986 accepted values correct. Its holdout validation will use a third fresh round of
invoices in Phase 4, so this 150-invoice set is not reused for a fix designed
after seeing it.

**Cost and limits.** Claude costs about $0.004 per invoice that needs it
($2 / $10 per million input / output tokens); a hard cap refuses any call
whose worst case could pass CPGOS_LLM_BUDGET_USD. The local model takes about
16 to 23 s per invoice on an M4 MacBook Pro; Claude about 2 s.

## Fix 2: real receipt photographs (CORD v2), step 1: the answer key and a label audit

Why: every FATURA invoice is generated from a template and its totals are
random, so nothing there is a photograph and nothing can be checked by
arithmetic. CORD v2 has 1,000 photographed Indonesian receipts with labelled
line items, subtotal, tax, and total (CC BY 4.0).

`make load-cord` extracts the images from the published Parquet files
(checked against `datasets.lock.json`), registers each one by its sha256 as a
`receipt` source document, and writes the answer key to `eval.receipt_gold`
(totals block, with boxes) and `eval.receipt_item_gold` (one row per line),
which the engine role cannot read. It runs in about 2 minutes and in bounded
memory: each published file is one 450-490 MB block that has to be decoded
whole, so a first version that held all 2.3 GB of images was killed for
running out of memory.

**Rupiah need their own parser.** "60.000" is sixty thousand; "45,000.00",
"9.500,00", "Rp. 12.000", "[25,000]" (a box around a payment) and "39,800)"
(a clipped accounting minus) all occur. The invoice parser would read
"60.000" as sixty. `parse_idr` reads all of these and refuses what it cannot
read for certain ("Rp 57,0000", "39,200,00", "121.000.00"): 21 of 4,047
labelled values stay unparsed rather than guessed.

**Two photos appear twice**, labelled by different hands (train 0058 = 0109,
train 0265 = 0285; one says "10,000" where the other says "10.000"). Both
pairs are inside train, so no test answer leaks into training. The loader
keeps the first and reports the copy; a duplicate across splits is refused
before anything is written. Result: 998 receipts (798 / 100 / 100).

**Do the published labels add up?** Before any "the arithmetic must check
out" rule is scored, the labels themselves were audited. The checks
understand three real receipt conventions found on train receipts: totals are
rounded (96,003 is printed 96,000), menu prices often include tax, and some
receipts print the change with a minus sign.

| Check (all splits) | Exact | Allowing Rp 100 rounding |
|---|---|---|
| Line amounts sum to the subtotal (or total) | 947 of 962 | 952 of 962 |
| Subtotal - discount + service (+ tax) = total | 581 of 638 | 620 of 638 |
| Cash - total = change | 512 of 527 | 515 of 527 |
| Quantity x unit price = line amount | 340 of 349 | 349 of 349 |
| **Every applicable check passes** | | **947 of 986 (96.0%)** |

The 39 receipts whose labels do not add up are genuine label problems, for
example test 0000 (a discount of 60,000 labelled on a receipt whose total is
60,000) and train 0290 (the tax labelled as a discount). Consequence for the
next step: an arithmetic check will be scored only on receipts whose labels
pass this audit, and the rest are reported separately, so a checker is never
blamed for an error in the answer key.

## Fix 2, step 2: reading the photos, with arithmetic as a witness

Two readers can share a blind spot (both misread a smudged 8 as a 3). A sum
does not: if the line amounts add up to the subtotal and the subtotal, tax,
and service add up to the total, a misread almost always breaks one of the
sums. So on receipts, arithmetic is the second witness.

- `make receipts-collect SPLIT=validation`: the local model reads every
  validation receipt (line items and totals, amounts copied as printed);
  recorded and resumable; prints no scores.
- `make receipts-eval SPLIT=validation`: Claude reads only the receipts whose
  local reading does not add up (spending cap and ledger as before), then
  three candidate rules are scored:
  1. `local_alone`: accept a local reading that adds up; a person reviews the rest;
  2. `local_then_claude`: also accept Claude's reading when it adds up;
  3. `local_then_claude_or_agreement`: also accept when both readers agree on
     the total and every line amount, even if the sums do not close.
- One rule is then frozen in `receipts.FROZEN_POLICY`; `receipts-eval
  SPLIT=test` refuses to run until it is, and scores only that rule.

Scored: totals precision, recall, and F1 per field; line items matched one to
one (same amount, name within 0.8 similarity) for precision, recall, and F1;
receipts with every amount right; receipts fully right; and for each rule the
straight-through rate and how often what went straight through was right.
Claims about arithmetic are also reported on the receipts whose labels add up.

### First validation run (report 16) and what it exposed

The local model read the 100 validation photos (27 s each). 71 readings
"added up", but only 23 of those were right. Two mistakes of mine, found by
breaking every wrong-but-accepted receipt down field by field and looking at
the photos:

1. **Amounts read as floats.** The schema allowed bare JSON numbers; a model
   that wrote `89.100` (89,100 rupiah) without quotes was read as 89.1. Every
   amount on such a receipt shrank 1,000 times and the sums still closed, so
   the arithmetic check passed it. Fix: amounts are read from the model's
   literal text (recordings keep it, so no re-run was needed), floats are
   refused, and a reading must also look like rupiah (whole numbers, total at
   least Rp 1,000), because sums can never catch a scale error.
2. **The model invented totals fields.** Asked for "the subtotal", it copied
   the total into it on 33 receipts that print no subtotal (receipt 0000), and
   invented a total on receipts that print only a subtotal (receipt 0029). A
   value rule cannot tell these apart: 182 train receipts really print
   subtotal = total.

Tried and dropped: checking each field's label with OCR. Tesseract cannot
read these photos (zero words on the clear receipt 0000 in its default mode),
so it missed the true label for about half the real values and discarded
correct readings (totals F1 fell from 0.88 to 0.56).

**Prompt 2.0: the model copies, code classifies.** The model now transcribes
every totals line as printed, `{"label": "TOTAL", "amount": "45,500"}`, and
the field comes from the label, using label words learned from train
receipts (CORD marks which printed words are labels; each label belongs to
the one field it names most often; the longest label on a line wins, so
"TOTAL" inside "SUBTOTAL" never counts). Held-out check on validation's own
printed labels: 361 of 373 map to the right field; the misses are rare
spellings and give no value rather than a wrong one. A field printed twice
with different amounts gives no value.

Scorer definitions, fixed to mean money: a printed "Discount 0" equals no
discount line; zero-price add-on lines carry no money; "card" includes
e-wallet payments, as the prompt says; and claims about arithmetic count only
receipts whose key has a total and adds up.

Report 16 stays on record as the result of prompt 1.0; prompt 2.0 needs a new
validation run before any rule is chosen.

### Validation report 17 (prompt 2.0) and the rule frozen for test

Prompt 2.0 on the same 100 photos: totals F1 0.88 -> 0.96, receipts with every
amount right 27 -> 73, and with the local model alone 63 of 70 straight-through
receipts right (was 23 of 71). The 10 wrong-but-accepted receipts (under any
rule) broke down as: cut-off or inflected labels ("Kembal", "CHANGES"), a
subtotal printed beside "ITEMS :1" with no word of its own (33 of 541 train
subtotals have none, so nothing reliable can be learned), a rare label
("NET1 AMT"), an add-on written as its own line, one real model miss
(receipt 0051: the card payment line skipped), and two answer-key gaps
confirmed on the photos (0034 prints "VISA 283,338", which the key lacks;
0096 prints the item "Choco Cheese 13,636", and the key has no items).

Changes (reading version 1.2), each with a test:
- printed counts ("TOTAL QTY 3", "ITEMS: 2") are recognised before labels are
  classified, so a count is never a second total;
- a label word cut short or inflected still matches (5+ letters);
- **an amount in the totals block whose label no rule understands sends the
  receipt to a person** (a bookkeeper does not sign off on an unexplained line);
- an add-on may be folded into its line or listed separately: same money.

Replayed from the recordings (no new model run):

| Rule | Straight through | Amounts right when accepted |
|---|---|---|
| local model alone | 71 of 100 | 69 of 71 (95% CI 90-99%) |
| local, then Claude | 83 of 100 | 80 of 83 (4 Claude reads not yet recorded) |
| local, then Claude, or agreement | 87 of 100 | 83 of 87 |

By the photos, 70 of the 71 local-alone acceptances are right (0096 is a key
gap); scores stay against the official key.

**Frozen for test: `local_then_claude`.** Every reading that skips a person,
from either model, must add up, account for every printed amount, and look
like rupiah. The agreement rule was rejected on principle: it accepts readings
whose sums do not close, which is how 0094 ("OTHERS 7,280" read as a card)
got through. Test is scored once, with this rule.

### Official test result (report 19, scored once with the frozen rule)

| | Validation (design) | **Test (official)** |
|---|---|---|
| Receipts straight through | 83 of 100 | **71 of 100** |
| Every amount right when accepted | 80 of 83 | **65 of 71 (91.5%, 95% CI 82.8-96.1%)** |
| Local model, every amount right | 80 of 100 | 60 of 100 |
| Answer key itself adds up | 96 of 100 | 90 of 100 |
| Claude, on the receipts it read | 18 of 29 right | 32 of 51 right (local: 17 of 51) |

Test is worse than validation, for two reasons stated plainly: the rule was
designed in three rounds of fixes made after looking at validation errors, so
part of the validation number is fitted to those 100 receipts; and the test
split is harder (its own key adds up less often, and the local model reads
fewer receipts fully right). 91.5% is the number to quote.

The 6 wrong acceptances, each looked at:
- **0074, a hole in my check:** two add-on lines had no price, which switched
  the line sum off entirely, and a line "FOOD 214,000" the model invented went
  through. Fixed in reading 1.3 (unpriced lines count as zero, so the sum is
  always checked), with a regression test from this receipt.
- **0016, my label classifier:** "Cash Payment" was classed as a card payment
  because the learned word PAYMENT is longer than CASH. Open: a fix needs a
  rule for generic words, and nothing left in CORD can score it.
- **0011, 0084, the model:** the payment line was skipped; every purchased
  line and the total were right.
- **0026, 0037, answer-key gaps:** the model read "Teh Tawar 2 x 4,000 =
  8,000", which the key leaves unpriced; the key has no total on 0037.

The fix in 1.3 is **not** scored on test: test has been seen. CORD has no
unseen receipts left (train taught the label words, validation shaped the
rule), so proving 1.3 needs another public receipt set; until then 1.3 is
covered by tests only.

## Round 3: the one-time score of routing 1.2 (report 20)

300 fresh holdout invoices (30 per never-seen layout, disjoint from rounds 1
and 2), Claude as third reader, $1.32 ($0.0044 per invoice):

| | Round 2, rule 1.1 (150) | **Round 3, rule 1.2 (300)** |
|---|---|---|
| Invoices with no person needed | 55.3% | **45.7%** (137, CI 40.1-51.3%) |
| Accepted values correct | 98.5% (1,819 / 1,846) | **99.4%** (3,614 / 3,635, CI 99.1-99.6%) |
| Fields sent to a person per invoice | 0.69 | 0.88 |
| Wrong acceptances | 27 | 21 |

1.2 traded straight-through for accuracy. Every invoice needed the third
reader, because the seller has no printed label, so its absence can only be
checked by another reader. 19 of the 21 wrong acceptances are one seller,
"Miller Inc", which no reader found (Claude included) and which was then
accepted as absent; 2 are values where Claude agreed with a wrong reader.

**Routing 1.3:** the seller joins invoice number, date, and total as fields
never accepted as absent. This is a business fact, not a fit to round 3:
every invoice names who issued it, so "no seller" is a reading failure.
It costs straight-through on invoices whose seller no reader can find, and
removes the error class that made up 19 of 21 mistakes. Each holdout layout
still has about 140 unseen invoices, so 1.3 gets its own one-time score on
round 4 (30 per layout, disjoint from rounds 1-3; a test enforces that rounds
never overlap).
