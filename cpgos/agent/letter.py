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

LETTER_VERSION = "letter-1.4.0"   # 1.3: dates checked in any form. 1.4: the deadline must be described truthfully

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?"
_WORD_DATES = [re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I),
               re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r",?\s+(\d{4})", re.I)]
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


def _month(name: str) -> int:
    n = name.lower().rstrip(".")
    return next(v for k, v in _MONTHS.items() if k.startswith(n[:3]))


def dates_in(text: str) -> set[str]:
    """Every date in free text as YYYY-MM-DD: "2016-09-26", "September 26, 2016", "26 Sep 2016"."""
    out = set(_DATES.findall(text))
    for m in _WORD_DATES[0].finditer(text):
        out.add(f"{int(m.group(3)):04d}-{_month(m.group(1)):02d}-{int(m.group(2)):02d}")
    for m in _WORD_DATES[1].finditer(text):
        out.add(f"{int(m.group(3)):04d}-{_month(m.group(2)):02d}-{int(m.group(1)):02d}")
    return out


def _strip_dates(text: str) -> str:
    for r in _WORD_DATES:
        text = r.sub(" ", text)
    return _DATES.sub(" ", text)


def numbers_in(text: str) -> set[str]:
    """Every number in free text, apart from dates and record ids (dates are checked separately, ids are ids)."""
    t = _strip_dates(_IDS.sub(" ", text))
    return {c for tok in _NUM.findall(t) if (c := _canon(tok)) is not None}


def allowed_numbers(*sources) -> set[str]:
    """Every number that appears anywhere in the structured facts and calculations a letter may cite."""
    return numbers_in(json.dumps(sources, default=str))


def verify(letter: str, *sources) -> list[str]:
    """Numbers and dates in the letter with no source. Empty means every figure and date is traceable.
    1.2 skipped dates entirely, so an invented deadline in YYYY-MM-DD form would have passed, and a correct
    date written as "September 26, 2016" was rejected as the numbers 26 and 2016 (local model, 2026-11-05)."""
    src = json.dumps(sources, default=str)
    ok_numbers, ok_dates = numbers_in(src), dates_in(src)
    return sorted([n for n in numbers_in(letter) if n not in ok_numbers] +
                  [d for d in dates_in(letter) if d not in ok_dates])


REQUEST_WHY = {   # the reason for a document request, as a retailer should read it
    "scan_back_pos_incomplete": "The POS data you sent is missing weeks in the claimed period",
    "shortage_no_receipt": "We have no receiving record for this delivery",
    "late_fee_no_delivery_record": "We have no delivery date on record for this shipment",
    "spoils_missing_fields": "The claim does not say which store and period it is for",
    "scan_back_missing_fields": "The claim does not say which store it is for",
}


def request_why(finding: dict) -> str:
    r = REQUEST_WHY.get(finding.get("engine_rule") or "")
    if r:
        return r
    t = finding.get("reason") or "We need this document to check the claim"
    return t[0].upper() + t[1:]


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


_ASK_BY = re.compile(r"\b(by|before|until|no later than|prior to|on or before)\b", re.I)
_SAYS_PASSED = re.compile(r"\b(has|have) (already )?(passed|expired|lapsed)\b|\bwas \d{4}-\d{2}-\d{2}\b", re.I)
_SAYS_NOT_PASSED = re.compile(r"\b(has not|hasn't|not yet|is not yet) (yet )?(passed|expired)\b", re.I)


def window_problems(text: str, claim: dict, as_of) -> list[str]:
    """What a letter says about the dispute deadline must be true on the ledger date.
    Found on the full run: a draft passed every number and date check yet asked for a document "by the
    dispute deadline of 2015-05-06", a year after that deadline (claim R-0000268)."""
    deadline = claim.get("dispute_deadline")
    if as_of is None or deadline is None:
        return []
    closed = str(deadline) < str(as_of)
    problems = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        names_it = str(deadline) in dates_in(sentence)
        if closed and names_it and _ASK_BY.search(sentence) and not _SAYS_PASSED.search(sentence):
            problems.append(f"asks for action by {deadline}, which has passed")
        if closed and _SAYS_NOT_PASSED.search(sentence):
            problems.append("says the deadline has not passed; it has")
        if not closed and _SAYS_PASSED.search(sentence) and not _SAYS_NOT_PASSED.search(sentence) and names_it:
            problems.append(f"says the deadline {deadline} has passed; it has not")
    return sorted(set(problems))


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
                f"Reason: {request_why(finding)}.\n\n")
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
    out = {"version": LETTER_VERSION, "source": "template", "text": fallback, "unsupported": verify(fallback, *sources),
           "window_problems": window_problems(fallback, claim, as_of)}
    if model is not None:
        text = model(claim=claim, finding=finding, calculations=calculations, supplier=supplier, partner=partner,
                     as_of=as_of)
        bad, wrong = verify(text, *sources), window_problems(text, claim, as_of)
        if bad or wrong:
            out["model_rejected"] = {"unsupported_numbers": bad, "window_problems": wrong}
        else:
            out.update(source="model", text=text, unsupported=[], window_problems=[])
    return out


# ---------- a model writes the letter; the check decides whether it may be used ----------

PROMPT_VERSION = "letter-prompt-1.2.0"   # 1.1: plain request reasons. 1.2: say what the evidence shows, not more
SYSTEM = ("You write short, firm, polite accounts-receivable letters from a food supplier to a distributor's "
          "deductions department. Use only the facts given. Never add a number, date, percentage, claim number, "
          "or deadline that is not in the facts. If deadline_has_passed is true, do not ask for anything by that date. "
          "State only what the facts say: a price that dropped like a promotion is not proof that a promotion ran. "
          "Plain text, no markdown, under 150 words, signed by the sender.")


def model_writer(provider):
    """A function that asks a model for the letter. The caller still checks every number in what comes back."""
    from cpgos.llm import LLMRequest

    def write(*, claim, finding, calculations, supplier, partner, as_of=None) -> str:
        deadline = claim.get("dispute_deadline")
        closed = as_of is not None and deadline is not None and str(deadline) < str(as_of)
        facts = {"from": supplier, "to": f"{partner} Deductions", "claim": claim.get("claim_number"),
                 "taken_on": str(claim.get("deduction_date")), "claim_amount": _money(claim["amount"]),
                 "decision": {"dispute": "we dispute it", "request": "we need a document before deciding",
                              "accept": "we accept it"}[finding["action"]],
                 "reason": why(finding, calculations) if finding["action"] == "dispute" else request_why(finding),
                 "document_needed": finding.get("request"),
                 "amount_to_repay": _money(finding["recoverable"]) if finding["action"] == "dispute" else None,
                 "dispute_deadline": str(deadline),
                 "deadline_has_passed": closed}
        prompt = ("Write the letter.\nFacts (JSON):\n" + json.dumps({k: v for k, v in facts.items() if v is not None},
                                                                   indent=1))
        return provider.complete(LLMRequest(system=SYSTEM, prompt=prompt, temperature=0.0, max_tokens=400)).text.strip()

    return write
