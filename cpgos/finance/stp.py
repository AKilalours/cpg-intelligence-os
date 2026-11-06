"""Risk-controlled straight-through processing (addition 11): autonomy is earned per lane.

Think of a new hire in accounts receivable. At first every match they make is checked. Once enough
of their work has been checked with almost no mistakes, they post on their own, and a manager still
spot-checks a few. If a spot-check finds a problem, they go back to having everything checked.

Here a lane is a kind of match (an exact reference, an explained short-pay, a one-keystroke typo),
and the record of checks belongs to one engine version, so a new release starts over. The decision
lives in the database (migration 0021); this module mirrors its arithmetic for the simulation and
reads lane status for the API. A test holds the two to the same numbers.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row, tuple_row

SIM_VERSION = "stp-sim-1.0.0"

# Only matches the engine would post automatically (confidence >= 0.90 with the full strategy) have a
# lane; everything below goes to a person regardless.
LANES = {0.99: "exact_reference", 0.98: "explained_short_pay", 0.95: "near_reference"}


def lane_of(confidence: float) -> str | None:
    return LANES.get(round(float(confidence), 2))


def upper_bound(k: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper-Pearson upper bound, by the same bisection as stp_upper_bound in SQL."""
    if n <= 0 or k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        p = (lo + hi) / 2
        term = (1 - p) ** n
        cdf = term
        for i in range(k):
            term = term * (n - i) / (i + 1) * p / (1 - p)
            cdf += term
        if cdf > 1 - conf:
            lo = p
        else:
            hi = p
    return hi


def reviews_needed(max_error_rate: float, conf: float = 0.95) -> int:
    """Clean reviews a lane needs before it may open (0 errors). 299 for 1% at 95%."""
    n = 1
    while upper_bound(0, n, conf) > max_error_rate:
        n += 1
    return n


def lane_status(conn: psycopg.Connection, lane: str, version: str) -> dict:
    row = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM stp_lane_status(%s, %s)", (lane, version)).fetchone()
    return dict(row)


def overview(conn: psycopg.Connection, version: str) -> list[dict]:
    """Each lane's policy, evidence and routing totals for one engine version, for the Today screen."""
    out = []
    pols = conn.cursor(row_factory=dict_row).execute(
        """SELECT lane, description, mode, max_error_rate, confidence, audit_rate, window_size, updated_by, updated_at
           FROM stp_policy ORDER BY lane""").fetchall()
    for p in pols:
        st = lane_status(conn, p["lane"], version)
        routed = dict(conn.cursor(row_factory=tuple_row).execute(
            """SELECT coalesce(routed, 'legacy'), count(*) FROM cash_application
               WHERE lane = %s AND engine_version = %s GROUP BY 1""", (p["lane"], version)).fetchall())
        need = reviews_needed(float(p["max_error_rate"]), float(p["confidence"]))
        out.append({**p, "reviews": st["reviews"], "errors": st["errors"], "upper_bound": st["upper_bound"],
                    "reversals": st["reversals"], "is_open": st["is_open"], "reviews_needed": need,
                    "routed": routed})
    return out


# ---------------------------------------------------------------- simulation

@dataclass(frozen=True)
class Item:
    """One suggestion the engine would post automatically, and whether it is right (from the answer key)."""
    line: int
    day: date
    lane: str
    correct: bool
    amount: Decimal
    version: str = "v"


@dataclass(frozen=True)
class Budget:
    max_error_rate: float = 0.01
    confidence: float = 0.95
    audit_rate: float = 0.05
    window: int = 1000
    review_delay_days: int = 2       # a person decides an audited line this many days later


def _u(salt: str, lane: str, line: int) -> float:
    h = hashlib.md5(f"{salt}:{lane}:{line}".encode()).hexdigest()[:8]
    return int(h, 16) / 4294967296.0


@dataclass
class _Lane:
    evidence: list = field(default_factory=list)     # (decided_day, correct)


def simulate(items: list[Item], budget: Budget = Budget(), salt: str = "sim") -> dict:
    """Replay a history day by day with the gate. Reviews arrive review_delay_days after the line, and
    only then count. The reviewer is the answer key (a perfect person): an assumption, stated."""
    lanes: dict[tuple[str, str], _Lane] = {}
    posted, audited = [], []
    opened: dict[str, str] = {}
    closed_after_open: dict[str, str] = {}
    state_open: dict[tuple[str, str], bool] = {}
    timeline = []
    wrong_so_far = 0
    for day in sorted({i.day for i in items}):
        for key, ln in lanes.items():
            seen = [c for d, c in ln.evidence if d <= day][-budget.window:]
            n, k = len(seen), sum(1 for c in seen if not c)
            is_open = n > 0 and upper_bound(k, n, budget.confidence) <= budget.max_error_rate
            was = state_open.get(key, False)
            if is_open and not was:
                opened.setdefault(f"{key[0]}@{key[1]}", str(day))
            if was and not is_open:
                closed_after_open.setdefault(f"{key[0]}@{key[1]}", str(day))
            state_open[key] = is_open
        for it in sorted((i for i in items if i.day == day), key=lambda i: i.line):
            key = (it.lane, it.version)
            ln = lanes.setdefault(key, _Lane())
            if state_open.get(key, False) and _u(salt, it.lane, it.line) >= budget.audit_rate:
                posted.append(it)
                wrong_so_far += not it.correct
            else:
                audited.append(it)
                ln.evidence.append((it.day + timedelta(days=budget.review_delay_days), it.correct))
        timeline.append({"day": str(day), "wrong_posted": wrong_so_far})
    total = sum((i.amount for i in items), Decimal(0))
    per_lane = {}
    for lane in sorted({i.lane for i in items}):
        mine = [i for i in items if i.lane == lane]
        per_lane[lane] = {"lines": len(mine), "posted": sum(1 for i in posted if i.lane == lane),
                          "audited": sum(1 for i in audited if i.lane == lane),
                          "wrong_posted": sum(1 for i in posted if i.lane == lane and not i.correct),
                          "wrong_in_lane": sum(1 for i in mine if not i.correct)}
    return {
        "lines": len(items),
        "posted_by_engine": len(posted),
        "sent_to_a_person": len(audited),
        "wrong_posted": sum(1 for i in posted if not i.correct),
        "wrong_caught_in_review": sum(1 for i in audited if not i.correct),
        "dollars_posted_share": round(float(sum((i.amount for i in posted), Decimal(0)) / total), 4) if total else None,
        "opened": opened,
        "closed_after_opening": closed_after_open,
        "per_lane": per_lane,
        "timeline": timeline,
    }


def no_gate(items: list[Item]) -> dict:
    """Today's rule: post everything at confidence >= 0.90."""
    return {"lines": len(items), "posted_by_engine": len(items), "sent_to_a_person": 0,
            "wrong_posted": sum(1 for i in items if not i.correct)}


def inject_fault(items: list[Item], lane: str, start: date, rate: float, seed: int = 7,
                 version: str | None = None) -> list[Item]:
    """From `start`, a fraction `rate` of a lane's right suggestions become wrong: a silent upstream
    change (a retailer starts quoting a PO number that collides with invoice numbers), or a bad release
    when `version` is set, which also gives those lines the new version so their evidence starts over."""
    rng = random.Random(seed)
    out = []
    for i in items:
        if i.day >= start and i.lane == lane:
            i = replace(i, correct=i.correct and rng.random() >= rate, **({"version": version} if version else {}))
        elif i.day >= start and version:
            i = replace(i, version=version)
        out.append(i)
    return out


def items_from(matches, truth: dict[int, int], version: str) -> list[Item]:
    """Engine matches (replayed) plus the answer key: line -> the invoice it truly pays."""
    out = []
    for m in matches:
        lane = lane_of(m.confidence) if m.decision == "auto" else None
        if lane is None or m.remittance_line_id not in truth:
            continue
        out.append(Item(m.remittance_line_id, m.payment_date, lane, m.invoice_id == truth[m.remittance_line_id],
                        Decimal(m.amount), version))
    return out
