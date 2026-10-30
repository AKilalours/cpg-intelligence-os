"""Turn printed invoice text into typed values, the same way for the answer key and for every extractor.

Analogy: two people copy the same receipt, one writes "20-Mar-2008" and the other
"2008-03-20". Both are right. Scoring compares typed values (a date, an exact
decimal, a currency code) so a correct read is never marked wrong for its
spelling, and a wrong read is never marked right because it looks similar.

Every function returns None when it cannot parse, never a guess.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Printed labels that precede a value, e.g. "Invoice Date:", "INVOICE #", "PO Number :".
_LABEL = re.compile(
    r"^\s*(?:invoice\s*(?:number|no\.?|#|id)|invoice\s*date|due\s*date|date|po\s*(?:number|no\.?|#)|"
    r"number|total|sub[_\s]?total|amount[_\s]?due|balance[_\s]?due)\s*[:#]?\s*",
    re.IGNORECASE,
)

_CURRENCY = {"$": "USD", "USD": "USD", "US$": "USD", "EUR": "EUR", "€": "EUR", "GBP": "GBP", "£": "GBP",
             "INR": "INR", "₹": "INR", "CAD": "CAD"}


def strip_label(text: str | None) -> str | None:
    if text is None:
        return None
    out = _LABEL.sub("", text.strip(), count=1).strip()
    return out or None


def parse_date(text: str | None) -> date | None:
    """Accepts the formats seen on invoices: 20-Mar-2008, 2008-03-20, 03/20/2008, 20.03.2008 (day first),
    March 20, 2008. Ambiguous all-numeric slashes are read month first (US) only when the day is > 12
    proves it; otherwise None, because guessing a date is worse than asking."""
    s = strip_label(text)
    if not s:
        return None
    s = s.strip().rstrip(".")
    m = re.fullmatch(r"(\d{1,2})[-\s/.]([A-Za-z]{3,9})[-\s/.,]*(\d{4})", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 >= b:
            return _safe(y, b, a)          # day first, proven
        if b > 12 >= a:
            return _safe(y, a, b)          # month first, proven
        return None                         # 03/04/2008: ambiguous, do not guess
    return None


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_currency(text: str | None) -> str | None:
    if not text:
        return None
    for token in re.findall(r"US\$|[A-Z]{3}|[$€£₹]", text):
        if token in _CURRENCY:
            return _CURRENCY[token]
    return None


def parse_amount(text: str | None, *, last: bool = False) -> Decimal | None:
    """The money amount in a printed line, as an exact Decimal.

    "TOTAL : 1,060.47 $" -> 1060.47; "TAX:VAT (3.85%):  10.26 $" -> 10.26 (percentages are skipped);
    "DISCOUNT(2.14%): (-)  9.39" -> 9.39. European "1.234,56" is recognised. With last=True the last
    amount is taken, otherwise the only one (several candidates without last=True -> None)."""
    if not text:
        return None
    body = re.sub(r"\(?\s*\d+(?:[.,]\d+)?\s*%\s*\)?", " ", text)     # drop rates like (3.85%)
    found = re.findall(r"\d{1,3}(?:[,.\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?", body)
    if not found:
        return None
    if len(found) > 1 and not last:
        return None
    return _to_decimal(found[-1])


def _to_decimal(tok: str) -> Decimal | None:
    tok = tok.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{1,2}", tok):       # 1.234,56
        tok = tok.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", tok):                       # 12,50
        tok = tok.replace(",", ".")
    else:
        tok = tok.replace(",", "")
    try:
        return Decimal(tok).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def normalize_id(text: str | None) -> str | None:
    """Invoice and PO numbers compare without their label, case, and surrounding spaces;
    the internal punctuation is part of the identifier and is kept."""
    s = strip_label(text)
    return re.sub(r"\s+", "", s).upper() if s else None


def normalize_name(text: str | None) -> str | None:
    """First line of a party block without its label: "Bill to:Denise Perez\\n16424 ..." -> "Denise Perez"."""
    if not text:
        return None
    s = re.sub(r"^\s*(?:bill[_\s]?to|ship[_\s]?to|buyer|send[_\s]?to|sold\s*to|customer)\s*:?\s*", "", text.strip(),
               flags=re.IGNORECASE)
    first = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    return re.sub(r"\s+", " ", first) or None


_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_VOCAB = set(_ONES) | set(_TENS) | set(_SCALE) | {"and", "point"}


def _split_known(w: str) -> list[str] | None:
    """"hundredand" -> ["hundred", "and"]; None when w is already a word or cannot be split."""
    if w in _VOCAB:
        return None
    for i in range(2, len(w) - 1):
        head, tail = w[:i], w[i:]
        if head in _VOCAB:
            rest = [tail] if tail in _VOCAB else _split_known(tail)
            if rest:
                return [head, *rest]
    return None


def words_to_amount(text: str | None) -> Decimal | None:
    """"seven hundred and thirt-\\ny-four point three three" -> 734.33. Used as an independent
    cross-check of the numeric total, never as the total itself."""
    if not text:
        return None
    s = re.sub(r"^.*?words\s*:?", "", text, flags=re.IGNORECASE | re.S)
    s = re.sub(r"-\s*\n\s*-?", "", s).lower()          # undo line-break hyphenation
    s = re.sub(r"[^a-z\s-]", " ", s).replace("-", " ")
    # A line-break hyphen can split one word ("twen-ty") or sit between two
    # ("hundred-and"); joining handles the first, this splits the second back.
    words = []
    for w in s.split():
        words.extend(_split_known(w) or [w])
    words = [w for w in words if w != "and"]
    if "point" in words:
        i = words.index("point")
        whole, frac = words[:i], words[i + 1:]
    else:
        whole, frac = words, []
    total, cur = 0, 0
    for w in whole:
        if w in _ONES:
            cur += _ONES[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur *= 100
        elif w in _SCALE:
            total += cur * _SCALE[w]
            cur = 0
        else:
            return None
    digits = []
    for w in frac:
        if w not in _ONES or _ONES[w] > 9:
            return None
        digits.append(str(_ONES[w]))
    if not whole and not digits:
        return None
    value = Decimal(total + cur)
    if digits:
        value += Decimal("0." + "".join(digits))
    return value.quantize(Decimal("0.01"))


# The canonical invoice header fields and their value types. Shared by every
# dataset loader and every extractor, so "invoice_date" means one thing everywhere.
FIELD_TYPES: dict[str, str] = {
    "invoice_number": "id",
    "invoice_date": "date",
    "due_date": "date",
    "po_number": "id",
    "seller_name": "text",
    "buyer_name": "name",
    "subtotal": "money",
    "discount": "money",
    "tax": "money",
    "total": "money",
    "amount_due": "money",
    "currency": "currency",
    "total_in_words": "money_words",
}


def normalize_field(field: str, raw: str | None) -> str | None:
    """Typed, canonical string for a field's printed text (label included or not), or None.
    The single conversion used for answer keys and for every extractor's output."""
    if raw is None or not raw.strip():
        return None
    kind = FIELD_TYPES[field]
    if kind == "id":
        return normalize_id(raw)
    if kind == "date":
        d = parse_date(raw)
        return d.isoformat() if d else None
    if kind == "money":
        a = parse_amount(raw, last=True)
        return str(a) if a is not None else None
    if kind == "money_words":
        a = words_to_amount(raw)
        return str(a) if a is not None else None
    if kind == "currency":
        return parse_currency(raw)
    if kind == "name":
        return normalize_name(raw)
    return re.sub(r"\s+", " ", raw).strip() or None
