"""Data contracts: checks every incoming row before it touches the ledger.

Analogy: a receiving dock. Each pallet (row) is inspected. A damaged pallet
goes to the quarantine shelf with a note saying what was wrong. If too many
pallets in one truck are damaged, the whole truck is refused, because that
usually means the source changed (new column order, new units), not that a
few rows are bad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                      # 'str' | 'int' | 'decimal' | 'date' | 'bool'
    required: bool = True
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    pattern: str | None = None
    allowed: frozenset[str] | None = None


@dataclass(frozen=True)
class Contract:
    name: str
    version: str
    columns: tuple[Column, ...]
    max_quarantine_rate: float = 0.02     # reject batch if more than 2% of rows fail
    min_rows: int = 1
    row_checks: tuple[tuple[str, Callable[[dict], bool]], ...] = ()


@dataclass
class ValidationResult:
    clean: list[dict] = field(default_factory=list)
    quarantined: list[tuple[int, dict, list[str]]] = field(default_factory=list)
    batch_errors: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.batch_errors

    @property
    def rows_in(self) -> int:
        return len(self.clean) + len(self.quarantined)


def _coerce(col: Column, raw: Any) -> tuple[Any, str | None]:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None, (f"{col.name}: missing" if col.required else None)
    s = str(raw).strip()
    try:
        if col.kind == "str":
            v: Any = s
        elif col.kind == "int":
            if not re.fullmatch(r"-?\d+", s):
                return None, f"{col.name}: not an integer ({s!r})"
            v = int(s)
        elif col.kind == "decimal":
            v = Decimal(s)
            if not v.is_finite():
                return None, f"{col.name}: not finite ({s!r})"
        elif col.kind == "date":
            v = date.fromisoformat(s)
        elif col.kind == "bool":
            if s.lower() not in {"true", "false", "1", "0"}:
                return None, f"{col.name}: not a boolean ({s!r})"
            v = s.lower() in {"true", "1"}
        else:
            raise ValueError(f"unknown kind {col.kind}")
    except (InvalidOperation, ValueError):
        return None, f"{col.name}: cannot parse {col.kind} from {s!r}"

    if col.min_value is not None and isinstance(v, (int, Decimal)) and v < col.min_value:
        return None, f"{col.name}: {v} below minimum {col.min_value}"
    if col.max_value is not None and isinstance(v, (int, Decimal)) and v > col.max_value:
        return None, f"{col.name}: {v} above maximum {col.max_value}"
    if col.pattern and not re.fullmatch(col.pattern, s):
        return None, f"{col.name}: {s!r} does not match {col.pattern}"
    if col.allowed is not None and s not in col.allowed:
        return None, f"{col.name}: {s!r} not in allowed set"
    return v, None


def validate(contract: Contract, header: list[str], rows: list[dict]) -> ValidationResult:
    res = ValidationResult()
    expected = [c.name for c in contract.columns]
    missing = [c for c in expected if c not in header]
    if missing:
        # Schema drift: fail fast and loudly, do not try to guess.
        res.batch_errors.append(f"schema drift: missing columns {missing}")
        return res

    for i, raw in enumerate(rows, start=1):
        typed: dict[str, Any] = {}
        errs: list[str] = []
        for col in contract.columns:
            v, err = _coerce(col, raw.get(col.name))
            if err:
                errs.append(err)
            typed[col.name] = v
        if not errs:
            for check_name, fn in contract.row_checks:
                try:
                    ok = fn(typed)
                except Exception as exc:  # a crashing check counts as a failed check
                    ok = False
                    check_name = f"{check_name} (raised {type(exc).__name__})"
                if not ok:
                    errs.append(f"row check failed: {check_name}")
        if errs:
            res.quarantined.append((i, raw, errs))
        else:
            res.clean.append(typed)

    if res.rows_in < contract.min_rows:
        res.batch_errors.append(f"too few rows: {res.rows_in} < {contract.min_rows}")
    elif res.rows_in and len(res.quarantined) / res.rows_in > contract.max_quarantine_rate:
        rate = len(res.quarantined) / res.rows_in
        res.batch_errors.append(
            f"quarantine rate {rate:.1%} exceeds {contract.max_quarantine_rate:.1%}; "
            "likely a source format change, batch rejected"
        )
    return res
