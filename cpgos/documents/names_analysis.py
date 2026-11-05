"""Should rule 1.5 accept a name the two AI readers agree on, even when OCR reads it differently?

Rule 1.4 lets two model readers settle an id, date, or amount that OCR could not read, but not a
name: names are where models most often "tidy up" what is printed (a missing suffix, a guessed
spelling). This measures, on development rounds only (already used to design rules, so measuring
them costs no fresh data), what each candidate rule would do:

  agree           accept when the local model and Claude read the same name
  agree_on_page   the same, and grounding found that name printed on the page for both readers

For each: how many name fields it would accept that 1.4 sends to a person, how many of those match
the answer key (with a 95% interval), and the wrong ones to read before deciding. A rule is worth
proposing only if its lower interval bound is at least the accepted-value accuracy rule 1.4 already
holds; it must then be scored once on a fresh round, like every rule before it.
"""

from __future__ import annotations

from dataclasses import dataclass

from cpgos.finance.evaluate import rate

NAME_FIELDS = ("seller_name", "buyer_name")


@dataclass(frozen=True)
class NameCase:
    doc: int
    field: str
    ocr: str | None
    local: str | None
    claude: str | None
    truth: str | None
    on_page: bool               # both model readings were found printed on the page by grounding


def is_candidate(c: NameCase) -> bool:
    """What 1.4 does not accept today: the two models agree, and OCR does not confirm it."""
    return c.local is not None and c.local == c.claude and c.ocr != c.local


def analyze(cases: list[NameCase], bar: float) -> dict:
    cand = [c for c in cases if c.field in NAME_FIELDS and is_candidate(c)]
    out = {"bar_lower_bound": bar, "name_fields_seen": sum(c.field in NAME_FIELDS for c in cases)}
    for name, keep in (("agree", lambda c: True), ("agree_on_page", lambda c: c.on_page)):
        acc = [c for c in cand if keep(c)]
        right = [c for c in acc if c.local == c.truth]
        r = rate(len(right), len(acc))
        out[name] = {"would_accept": len(acc), "right": r,
                     "worth_proposing": bool(r["ci95"]) and r["ci95"][0] >= bar,
                     "wrong": [{"doc": c.doc, "field": c.field, "models": c.local, "ocr": c.ocr, "truth": c.truth}
                               for c in acc if c.local != c.truth][:25]}
    return out
