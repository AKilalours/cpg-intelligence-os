from decimal import Decimal

import pytest

from cpgos.money import CurrencyMismatchError, Money, total


def test_float_is_rejected():
    # 0.1 + 0.2 != 0.3 in floats; money must never start as a float.
    with pytest.raises(TypeError):
        Money.of(0.1)


def test_decimal_sum_is_exact():
    assert Money.of("0.10") + Money.of("0.20") == Money.of("0.30")


def test_bankers_rounding():
    assert Money.of("2.675").amount == Decimal("2.68")   # half to even -> 8
    assert Money.of("2.665").amount == Decimal("2.66")   # half to even -> 6


def test_currency_mismatch_raises():
    with pytest.raises(CurrencyMismatchError):
        Money.of("1", "USD") + Money.of("1", "EUR")


@pytest.mark.parametrize("amount,weights", [
    ("100.00", [1, 1, 1]),
    ("0.05", [1, 1, 1, 1, 1, 1, 1]),
    ("1234.57", ["0.5", "0.3", "0.2"]),
    ("-100.00", [1, 1, 1]),
])
def test_allocation_never_loses_a_cent(amount, weights):
    original = Money.of(amount)
    parts = original.allocate(weights)
    assert total(parts) == original


def test_allocation_is_deterministic():
    assert [p.amount for p in Money.of("100").allocate([1, 1, 1])] == [
        Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


def test_zero_weights_rejected():
    with pytest.raises(ValueError):
        Money.of("10").allocate([0, 0])
