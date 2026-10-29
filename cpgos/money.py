"""Exact money arithmetic.

Analogy: a cash register never stores $0.1 + $0.2 as 0.30000000000000004.
Floats do. In reconciliation that tiny error becomes "unmatched $0.01" on
thousands of lines, so floats are banned at the type level here.

Rules:
- Money is built only from Decimal, int, or str. Passing a float raises.
- Amounts are rounded to the currency's minor unit with ROUND_HALF_EVEN
  (banker's rounding), the convention that avoids systematic upward bias.
- Different currencies never mix silently.
- Splitting an amount (e.g. one payment across 3 invoices) never loses or
  creates a cent: the parts always sum exactly to the original.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

MINOR_UNITS = {"USD": 2, "CAD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
ROUNDING = ROUND_HALF_EVEN


class CurrencyMismatchError(ValueError):
    pass


def _quantum(currency: str) -> Decimal:
    try:
        return Decimal(1).scaleb(-MINOR_UNITS[currency])
    except KeyError as exc:
        raise ValueError(f"Unsupported currency {currency!r}") from exc


def to_decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise TypeError("Floats are not allowed for money. Pass a str or Decimal, e.g. '19.99'.")
    if isinstance(value, bool):
        raise TypeError("bool is not a money amount")
    try:
        d = Decimal(value) if not isinstance(value, Decimal) else value
    except InvalidOperation as exc:
        raise ValueError(f"Not a valid amount: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"Amount must be finite: {value!r}")
    return d


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        q = to_decimal(self.amount).quantize(_quantum(self.currency), rounding=ROUNDING)
        object.__setattr__(self, "amount", q)

    @classmethod
    def of(cls, value: Decimal | int | str, currency: str = "USD") -> "Money":
        return cls(to_decimal(value), currency)

    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(f"{self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def times(self, factor: Decimal | int | str) -> "Money":
        return Money(self.amount * to_decimal(factor), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def allocate(self, weights: list[Decimal | int | str]) -> list["Money"]:
        """Split by weights with no lost cents (largest remainder method).

        Example: $100.00 split 3 ways -> $33.34, $33.33, $33.33 (sums to $100.00).
        """
        ws = [to_decimal(w) for w in weights]
        if not ws or any(w < 0 for w in ws) or sum(ws) == 0:
            raise ValueError("weights must be non-empty, non-negative, and not all zero")
        q = _quantum(self.currency)
        total_units = int((self.amount / q).to_integral_value())
        sign = -1 if total_units < 0 else 1
        total_units = abs(total_units)
        wsum = sum(ws)
        raw = [Decimal(total_units) * w / wsum for w in ws]
        floors = [int(r) for r in raw]
        leftover = total_units - sum(floors)
        # Give remaining cents to the largest fractional parts; ties go to the
        # earliest index so the result is deterministic.
        order = sorted(range(len(ws)), key=lambda i: (-(raw[i] - floors[i]), i))
        for i in order[:leftover]:
            floors[i] += 1
        return [Money(Decimal(sign * u) * q, self.currency) for u in floors]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


def total(items: list[Money], currency: str = "USD") -> Money:
    acc = Money.of(0, currency)
    for m in items:
        acc = acc + m
    return acc
