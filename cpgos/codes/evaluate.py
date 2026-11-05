"""Accuracy and macro-F1 with 95% intervals, on seen phrasings (validation) and unseen ones (held-out)."""

from __future__ import annotations

from collections import Counter

from cpgos.codes.data import CATEGORIES, Example
from cpgos.finance.evaluate import rate


def score(predict, examples: list[Example]) -> dict:
    pred = [predict(e.text) for e in examples]
    right = sum(p == e.category for p, e in zip(pred, examples))
    f1s = {}
    for c in CATEGORIES:
        tp = sum(p == c and e.category == c for p, e in zip(pred, examples))
        fp = sum(p == c and e.category != c for p, e in zip(pred, examples))
        fn = sum(p != c and e.category == c for p, e in zip(pred, examples))
        f1s[c] = round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else None
    by_family = Counter((e.family, p == e.category) for p, e in zip(pred, examples))
    worst = sorted({e.family for e in examples},
                   key=lambda f: by_family[(f, True)] / max(1, by_family[(f, True)] + by_family[(f, False)]))[:5]
    return {"n": len(examples), "accuracy": rate(right, len(examples)),
            "macro_f1": round(sum(v for v in f1s.values() if v is not None) / len(CATEGORIES), 4),
            "no_answer": sum(p is None for p in pred), "f1_by_category": f1s,
            "hardest_phrasings": [{"family": f, "right": by_family[(f, True)],
                                   "of": by_family[(f, True)] + by_family[(f, False)]} for f in worst]}
