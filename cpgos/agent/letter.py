"""Dispute and request letters, and the check that keeps every number in them true.

Analogy: a fact-checker who will not let a letter go out with a figure they cannot trace. Every
amount, unit count, and percentage in a letter must appear in the agent's facts or in a calculation
row it cites. A letter that fails is not sent; the reviewer sees exactly which number has no source.
The same check applies whether the letter was filled from a template ($0, always available) or
written by a language model (which can produce a plausible number from nowhere).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

LETTER_VERSION = "letter-1.2.0"   # 1.1: no past deadlines. 1.2: disputes state the fact from the records, not the rule name

_IDS = re.compile(r"\b(?:R-\d+|NP\d+|[A-Z]{2}_\d+|FOODS_\d+_\d+|ACH\d+)\b")      # claim, invoice, store, item ids
_DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUM = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?%?")


def _canon(tok: str) -> str | None:
    t = tok.replace("$", "").replace(",", "").rstrip("%")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return format(d.normalize(), "f") if d == d.to_integral() else format(d.quantize(Decimal("0.01")), "f")


def numbers_in(text: str) -> set[str]:
    """Every number in free text, ignoring dates and record ids (which are checked as ids, not amounts)."""
    t = _DATES.sub(" ", _IDS.sub(" ", text))
    return {c for tok in _NUM.findall(t) if (c := _canon(tok)) is not None}


def allowed_numbers(*sources) -> set[str]:
    """Every number that appears anywhere in the structured facts and calculations a letter may cite."""
    return numbers_in(json.dumps(sources, default=str))


def verify(letter: str, *sources) -> list[str]:
    """Numbers in the letter with no source. Empty means every figure is traceable."""
    ok = allowed_numbers(*sources)
    return sorted(n for n in numbers_in(letter) if n not in ok)


def why(finding: dict, calculations: list[dict]) -> str:
    """The reason in the words of an AR analyst, built only from the evidence the case cites."""
    calc = next((c for c in calculations if (c.get("inputs") or {}).get("engine_evidence") is not None), None)
    ev = (calc or {}).get("inputs", {}).get("engine_evidence") or {}
    rule = (calc or {}).get("inputs", {}).get("engine_rule") or finding.get("engine_rule") or ""
    g = lambda k: ev.get(k)
    earlier = g("earlier_claims")
    if earlier:
        return f"It repeats {', '.join(earlier)}, already taken for the same item"
    if rule == "shortage_not_on_receipt" and g("received_units") is not None:
        return (f"Our receiving record for invoice {g('invoice_number')} shows {g('received_units')} of "
                f"{g('shipped_units')} units received, so there was no shortage")
    if rule == "shortage_exceeds_receipt" and g("received_units") is not None:
        return (f"Invoice {g('invoice_number')}: {g('shipped_units')} units shipped and {g('received_units')} received; "
                f"the receipt supports {_money(g('supported_amount'))} of this claim")
    if rule == "late_fee_on_time_delivery" and g("delivered"):
        return (f"Invoice {g('invoice_number')} was scheduled for {g('scheduled')} and delivered on {g('delivered')}, "
                "on time, so no late fee applies")
    if rule == "late_fee_above_published_fee" and g("published_fee"):
        return f"The published late delivery fee is {_money(g('published_fee'))}; this claim is more than that"
    if rule.endswith("_duplicate"):
        return "This repeats an earlier claim for the same promotion, which has already been paid in full"
    if rule == "scan_back_no_agreement":
        return "We have no promotion agreement for this store, item, and period, and the shelf price shows no promotion"
    if rule == "scan_back_overclaim" and g("supported_amount"):
        return (f"The retailer's POS supports {_money(g('supported_amount'))} for this promotion; "
                f"the claim is {_money(g('claimed_amount'))}")
    if rule == "spoils_over_allowance" and g("allowed_amount"):
        return (f"The spoils allowance for this store and period is {_money(g('allowed_amount'))} "
                f"({g('allowance_pct')} of {_money(g('invoiced_value'))} invoiced)")
    r = finding.get("reason") or "the records do not support it"
    return r[0].upper() + r[1:]


def _money(x) -> str:
    return f"${Decimal(str(x)):,.2f}"


def template(claim: dict, finding: dict, supplier: str, partner: str, as_of=None,
             calculations: list[dict] | None = None) -> str:
    """The letter a careful AR analyst would send, from the agent's finding only. When the dispute window
    has closed (as_of after the deadline), it says so instead of asking for anything by a past date."""
    deadline = claim.get("dispute_deadline")
    closed = as_of is not None and deadline is not None and str(deadline) < str(as_of)
    calculations = calculations or []
    head = (f"To: {partner} Deductions\nFrom: Accounts Receivable, {supplier}\n"
            f"Re: Claim {claim['claim_number']}, taken {claim['deduction_date']}, {_money(claim['amount'])}\n\n")
    if finding["action"] == "dispute":
        body = (f"We dispute this deduction. {why(finding, calculations)}.\n\n"
                f"Amount we ask you to repay: {_money(finding['recoverable'])}.\n")
        body += "\nThe calculation and the records it uses are attached. "
        body += (f"The dispute deadline ({deadline}) has passed; we raise this for the record and ask you to "
                 "repay it as a matter of goodwill.\n" if closed else
                 "Please repay the amount above on your next remittance, or send the documents that support "
                 f"the full claim before the dispute deadline of {deadline}.\n")
    elif finding["action"] == "request":
        body = (f"Before we accept or dispute this deduction we need one document: {finding['request']}.\n\n"
                f"Reason: {finding['reason'][0].upper() + finding['reason'][1:]}.\n\n")
        body += (f"The dispute deadline ({deadline}) has passed, so we ask for it for our records.\n" if closed else
                 f"Please send it before {deadline}. Until then we hold the claim open and take no action on it.\n")
    else:
        body = "We have checked this deduction against our records and accept it. No action is needed.\n"
    return head + body + f"\nRegards,\nAccounts Receivable, {supplier}\n"


def draft(claim: dict, finding: dict, calculations: list[dict], supplier: str, partner: str,
          model=None, as_of=None) -> dict:
    """A letter and its check. With a model, its draft is used only if every number in it has a source;
    otherwise the template is used and the model's unsupported numbers are reported."""
    sources = (claim, finding.get("facts"), str(finding.get("recoverable")), calculations, finding.get("request"))
    fallback = template(claim, finding, supplier, partner, as_of, calculations)
    out = {"version": LETTER_VERSION, "source": "template", "text": fallback, "unsupported": verify(fallback, *sources)}
    if model is not None:
        text = model(claim=claim, finding=finding, calculations=calculations, supplier=supplier, partner=partner)
        bad = verify(text, *sources)
        if bad:
            out["model_rejected"] = {"unsupported_numbers": bad}
        else:
            out.update(source="model", text=text, unsupported=[])
    return out
