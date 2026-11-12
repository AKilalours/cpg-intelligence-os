"""Drift monitors: a smoke alarm on what comes in, needing no answers.

The error budget learns only from spot-checks, so it notices a silent fault after dozens of lines.
Many faults are not silent in the inputs: a payer that starts quoting PO numbers, a wave of
short-pays, deduction reasons worded in ways the reader has never seen, a retailer price war. These
change what the inputs look like before anyone has checked a single answer.

Each stream (cash lines, deduction claims, shelf prices) is described by a few categorical features
anyone can read: the shape of a reference ("INV-9999999"), which lane the engine chose, the size of
an amount, whether the reason reader was sure. Once a week, the latest window is compared with the
baseline the system's trust was earned on. An alarm needs both:
  - statistical significance: a chi-square test of homogeneity, p below alpha; and
  - practical significance: a population stability index (PSI) of at least min_psi,
because with thousands of price rows a test finds "significant" changes nobody would act on.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

DRIFT_VERSION = "drift-1.0.0"


# ------------------------------------------------------------------ statistics

def _gammq(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) (series below a+1, continued fraction above)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return max(0.0, 1.0 - s * math.exp(-x + a * math.log(x) - gln))
    b, c, d = x + 1 - a, 1e300, 1.0 / (x + 1 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - gln) * h)


def chi2_sf(x: float, df: int) -> float:
    """P(chi-square with df degrees of freedom > x)."""
    return 1.0 if df <= 0 else _gammq(df / 2.0, x / 2.0)


def psi(base: dict[str, float], cur: dict[str, float], eps: float = 1e-4) -> float:
    """Population stability index between two distributions over the same categories."""
    out = 0.0
    for k in set(base) | set(cur):
        b, c = max(base.get(k, 0.0), eps), max(cur.get(k, 0.0), eps)
        out += (c - b) * math.log(c / b)
    return out


@dataclass
class Check:
    feature: str
    n: int
    p_value: float
    psi: float
    alarm: bool
    moved: list[dict] = field(default_factory=list)   # the categories that moved most, for a person to read


def compare(feature: str, base_counts: Counter, window: list[str], alpha: float, min_psi: float,
            min_share: float = 0.02) -> Check:
    """Window against baseline. Categories under min_share of the baseline (and anything the baseline
    never saw) are pooled as 'other', so a brand-new reference shape counts against 'other'."""
    total = sum(base_counts.values())
    keep = {k for k, v in base_counts.items() if v / total >= min_share}
    def fold(c: Counter) -> Counter:
        out = Counter()
        for k, v in c.items():
            out[k if k in keep else "other"] += v
        return out
    b, w = fold(base_counts), fold(Counter(window))
    cats = sorted(set(b) | set(w) | {"other"})
    n = len(window)
    # Baseline shares with half a count added to every category, so a category the baseline never had
    # has a small but nonzero expectation instead of an undefined one.
    bt = total + 0.5 * len(cats)
    bp = {k: (b.get(k, 0) + 0.5) / bt for k in cats}
    wp = {k: w.get(k, 0) / n for k in cats} if n else {}
    stat = sum((w.get(k, 0) - n * bp[k]) ** 2 / (n * bp[k]) for k in cats) if n else 0.0
    p = chi2_sf(stat, len(cats) - 1)
    s = psi(bp, wp) if n else 0.0
    moved = sorted(({"value": k, "baseline": round(bp[k], 4), "now": round(wp.get(k, 0.0), 4)} for k in cats),
                   key=lambda m: -abs(m["now"] - m["baseline"]))[:3]
    return Check(feature, n, p, s, bool(n) and p < alpha and s >= min_psi, moved)


# ------------------------------------------------------------------ streams

@dataclass(frozen=True)
class Event:
    day: date
    features: tuple   # ((name, value), ...): hashable, so streams can be compared in tests


def feats(e: Event) -> dict[str, str]:
    return dict(e.features)


@dataclass(frozen=True)
class Policy:
    """Fixed before any result was seen (decision 97)."""
    alpha: float = 1e-4          # per feature per weekly check
    min_psi: float = 0.10        # the usual "moderate shift" line
    window: int = 60             # most recent events compared
    min_window: int = 30         # no verdict on fewer
    baseline_share: float = 0.4  # the first 40% of the history is the baseline


def shape(ref: str | None) -> str:
    """'INV-0012345' -> 'A-9': letters become A, digits 9, runs collapsed. Keeps the format, drops the value."""
    if not ref or not ref.strip():
        return "none"
    s = re.sub(r"[A-Za-z]+", "A", ref.strip())
    s = re.sub(r"[0-9]+", "9", s)
    return re.sub(r"\s+", " ", s)


def amount_bucket(x) -> str:
    v = float(x)
    return "<=0" if v <= 0 else f"2^{int(math.floor(math.log2(v)))}"


def cash_events(lines, matches) -> list[Event]:
    """One event per cash line: reference shape, the engine's lane, amount size, and whether it was netted."""
    from cpgos.finance.stp import lane_of
    by = {m.remittance_line_id: m for m in matches}
    out = []
    for rl, day, net, ref, _p, gross, deducted, _code in lines:
        m = by.get(rl)
        lane = (lane_of(m.confidence) or "review") if m and m.decision == "auto" else (m.decision if m else "unmatched")
        out.append(Event(day, (("reference_shape", shape(ref)), ("engine_outcome", lane),
                               ("amount_size", amount_bucket(gross)),
                               ("netted_short_pay", "yes" if float(deducted or 0) > 0 else "no"))))
    return out


def deduction_events(rows, reader, threshold: float) -> list[Event]:
    """rows: (day, reason text, code). The reader is the character n-gram model; a reason it is not
    sure about (margin under the hybrid's threshold) would go to the slower model or a person."""
    out = []
    for day, text, code in rows:
        sure = reader.margin(text or "") >= threshold
        out.append(Event(day, (("reason_reader_sure", "yes" if sure else "no"),
                               ("reason_category", reader.predict(text or "") if sure else "unsure"),
                               ("code", code or "none"))))
    return out


def price_events(rows, prior_weeks: int = 8, drop: float = 0.9) -> list[Event]:
    """rows: (week, sku, store, price), any order. One event per row with 4+ prior weeks: is this week
    priced at or below 90% of the item's high over the prior 8 weeks (the promotion signal)."""
    series: dict[tuple, list] = {}
    for week, sku, store, price in sorted(rows, key=lambda r: (r[1], r[2], r[0])):
        if price is None:
            continue
        series.setdefault((sku, store), []).append((week, float(price)))
    out = []
    for pts in series.values():
        for i, (week, price) in enumerate(pts):
            prior = [p for _, p in pts[max(0, i - prior_weeks):i]]
            if len(prior) >= 4:
                out.append(Event(week, (("shelf_price_drop", "yes" if price <= drop * max(prior) else "no"),)))
    return sorted(out, key=lambda e: e.day)


@dataclass
class Alarm:
    stream: str
    day: date
    check: Check


def watch(stream: str, events: list[Event], policy: Policy = Policy(), window: int | None = None,
          step_days: int = 7) -> tuple[list[Alarm], date | None]:
    """Weekly checks after the baseline period. Returns every alarm and the baseline's last day.
    window overrides policy.window (price streams have many rows per week)."""
    if not events:
        return [], None
    events = sorted(events, key=lambda e: e.day)
    days = sorted({e.day for e in events})
    base_end = days[int(len(days) * policy.baseline_share)]
    base = [e for e in events if e.day <= base_end]
    names = [k for k, _ in base[0].features] if base else []
    base_counts = {f: Counter(feats(e)[f] for e in base) for f in names}
    w = window or policy.window
    alarms = []
    day = base_end + timedelta(days=step_days)
    later = [e for e in events if e.day > base_end]
    i = 0
    while day <= days[-1] + timedelta(days=step_days - 1):
        while i < len(later) and later[i].day <= day:
            i += 1
        recent = later[max(0, i - w):i]
        if len(recent) >= policy.min_window:
            for f in names:
                c = compare(f, base_counts[f], [feats(e)[f] for e in recent], policy.alpha, policy.min_psi)
                if c.alarm:
                    alarms.append(Alarm(stream, day, c))
        day += timedelta(days=step_days)
    return alarms, base_end
