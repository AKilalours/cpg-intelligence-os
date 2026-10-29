from decimal import Decimal

from cpgos.contracts import Column, Contract, validate

C = Contract(
    name="t", version="1",
    columns=(
        Column("invoice_number", "str"),
        Column("amount", "decimal", min_value=Decimal("0")),
        Column("invoice_date", "date"),
    ),
    max_quarantine_rate=0.34,
    row_checks=(("amount under 1M", lambda r: r["amount"] < 1_000_000),),
)
HEADER = ["invoice_number", "amount", "invoice_date"]


def test_clean_rows_pass_typed():
    res = validate(C, HEADER, [{"invoice_number": "INV-1", "amount": "10.50", "invoice_date": "2026-01-02"}])
    assert res.accepted and res.clean[0]["amount"] == Decimal("10.50")


def test_bad_row_is_quarantined_with_reasons():
    rows = [
        {"invoice_number": "INV-1", "amount": "10", "invoice_date": "2026-01-02"},
        {"invoice_number": "INV-2", "amount": "10", "invoice_date": "2026-01-03"},
        {"invoice_number": "INV-3", "amount": "-5", "invoice_date": "02/03/2026"},
    ]
    res = validate(C, HEADER, rows)
    assert res.accepted
    assert len(res.clean) == 2
    row_no, _, reasons = res.quarantined[0]
    assert row_no == 3 and len(reasons) == 2


def test_high_failure_rate_rejects_whole_batch():
    rows = [{"invoice_number": "X", "amount": "abc", "invoice_date": "2026-01-01"}] * 2 + [
        {"invoice_number": "Y", "amount": "1", "invoice_date": "2026-01-01"}]
    assert not validate(C, HEADER, rows).accepted


def test_schema_drift_fails_fast():
    res = validate(C, ["invoice_no", "amount", "invoice_date"], [])
    assert not res.accepted and "schema drift" in res.batch_errors[0]


def test_row_check_applies():
    res = validate(C, HEADER, [{"invoice_number": "Z", "amount": "5000000", "invoice_date": "2026-01-01"},
                               {"invoice_number": "A", "amount": "1", "invoice_date": "2026-01-01"},
                               {"invoice_number": "B", "amount": "1", "invoice_date": "2026-01-01"}])
    assert "row check failed" in res.quarantined[0][2][0]
