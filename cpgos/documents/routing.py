"""Step 6: which extracted values may skip human review. Rule: two independent readers must agree.

Analogy: a bank's four-eyes rule. A payment goes out without a manager only
when two people independently keyed the same amount. Here the "people" are
three readers that fail in different ways: OCR + rules (misreads characters),
a local vision model (confuses roles), and Claude (a stronger model, called
only when needed).

Evidence for the rule (calibration and round-1 holdout, report 8): when OCR +
rules and the local model both answer and agree, they were right on 1,067 of
1,067 holdout fields and 462 of 462 calibration fields; when they disagree,
OCR + rules was right only 3 of 19 times.

Per field:
- reader 1 and reader 2 agree                       -> accept ("two readers agree")
- both are blank, the field is optional, and none of
  its labels is printed anywhere on the page         -> accept as absent (absence is verified,
                                                        not assumed: version 1.1)
- otherwise ask reader 3 (Claude) for the invoice; if it agrees with one of
  the others                                         -> accept that value
- otherwise                                          -> a person decides, shown all candidates
Required fields (invoice number, invoice date, total) are never accepted as absent.
An invoice goes straight through only when every field is accepted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cpgos.documents import normalize as nz

POLICY_VERSION = "two-readers-1.1.0"
REQUIRED = ("invoice_number", "invoice_date", "total")


@dataclass
class Decision:
    field_name: str
    value: str | None
    status: str                  # "accepted" | "review"
    reason: str
    candidates: dict             # reader -> value


def absence_vocabulary(rules_model) -> dict[str, list[str]]:
    """Every label ever used for a field in training, including labels the learner set aside as
    ambiguous (e.g. "BALANCE_DUE", which some layouts use for the total and others for the amount due)."""
    vocab = {f: [lab for lab, _ in labs] for f, labs in rules_model.lexicon.items()}
    for item in rules_model.trained_on.get("labels_reassigned_or_dropped", []):
        if item.endswith(")") and " (" in item:
            lab, f = item[:-1].rsplit(" (", 1)
            vocab.setdefault(f, []).append(lab)
    return vocab


def labels_on_page(words, vocab: dict[str, list[str]]) -> set[str]:
    """Fields whose label is printed somewhere on the page."""
    from cpgos.documents.ocr import lines
    from cpgos.documents.rules import label_pattern
    text = "\n".join(t for t, _ in lines(words))
    return {f for f, labs in vocab.items() if any(label_pattern(lab).search(text) for lab in labs)}


def needs_third_reader(r1: dict, r2: dict, printed_labels: set[str] = frozenset()) -> list[str]:
    """Fields the first two readers did not settle."""
    out = []
    for f in nz.FIELD_TYPES:
        a, b = r1.get(f), r2.get(f)
        if a is not None and a == b:
            continue
        if a is None and b is None and f not in REQUIRED and f not in printed_labels:
            continue
        out.append(f)
    return out


def decide(r1: dict, r2: dict, r3: dict | None = None, printed_labels: set[str] = frozenset()) -> list[Decision]:
    out = []
    for f in nz.FIELD_TYPES:
        a, b = r1.get(f), r2.get(f)
        c = r3.get(f) if r3 is not None else None
        cands = {k: v for k, v in (("rules", a), ("local_model", b), ("claude", c)) if v is not None}
        if a is not None and a == b:
            out.append(Decision(f, a, "accepted", "two readers agree", cands))
        elif a is None and b is None and f not in REQUIRED and (r3 is None or c is None) and f not in printed_labels:
            out.append(Decision(f, None, "accepted", "absent: no value and no label for it on the page", cands))
        elif c is not None and c in (a, b):
            out.append(Decision(f, c, "accepted", "third reader agrees with one of two", cands))
        else:
            reason = ("required field not found" if not cands and f in REQUIRED else
                      "label printed but no value read" if not cands and f in printed_labels else
                      "readers disagree" if len(set(cands.values())) > 1 else
                      "only one reader found it" if cands else "no reader found it")
            out.append(Decision(f, None, "review", reason, cands))
    return out


def summarize(decisions_by_doc: dict[int, list[Decision]], gold: dict[int, dict] | None = None) -> dict:
    """Straight-through rate, review load, and (with an answer key) the precision of accepted values."""
    fields, accepted = Counter(), Counter()
    stp = 0
    for sid, ds in decisions_by_doc.items():
        stp += all(d.status == "accepted" for d in ds)
        for d in ds:
            fields[d.status] += 1
            fields[f"{d.status}: {d.reason}"] += 1
            if gold is not None and d.status == "accepted":
                g = gold.get(sid, {}).get(d.field_name)
                ok = d.value == g
                accepted["correct" if ok else "wrong"] += 1
                accepted[f"{'correct' if ok else 'wrong'}: {d.reason}"] += 1
    from cpgos.finance.evaluate import rate
    n_docs = len(decisions_by_doc)
    out = {"invoices": n_docs, "straight_through": rate(stp, n_docs),
           "fields": dict(fields), "review_fields_per_invoice": round(fields["review"] / n_docs, 2) if n_docs else None}
    if gold is not None:
        n_acc = accepted["correct"] + accepted["wrong"]
        out["accepted_correct"] = rate(accepted["correct"], n_acc)
        out["accepted_by_reason"] = {k: v for k, v in accepted.items() if ":" in k}
    return out
