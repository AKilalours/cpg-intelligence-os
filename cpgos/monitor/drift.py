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

DRIFT_VERSION = "drift-1.2.0"   # 1.1: moving baseline (decision 99). 1.2: two-sample test, merged sparse categories (decision 100)


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


def _lchoose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_two_sided(x: int, m: int, n: int, k: int) -> float:
    """Exact two-sample test for one value: of k pooled occurrences, x fell in the window of n (baseline
    of m). Two-sided as twice the smaller tail. Valid for any counts, including a value seen 3 times.

    Exact at every size: a chi-square shortcut for large counts was tried and was ten times too small in
    the far tail (where alarms live), the same kind of error that caused 1.0's false alarms. The tail is
    summed outward from x by the ratio of neighbouring terms and stops once terms are negligible."""
    lo, hi = max(0, k - m), min(k, n)
    if hi <= lo:
        return 1.0
    x = min(max(x, lo), hi)
    term = math.exp(_lchoose(k, x) + _lchoose(m + n - k, n - x) - _lchoose(m + n, n))
    tail = term
    if x >= k * n / (m + n):
        i, t = x, term
        while i < hi:
            t *= (k - i) * (n - i) / ((i + 1) * (m - k + i + 1))
            i += 1
            tail += t
            if t < tail * 1e-16:
                break
    else:
        i, t = x, term
        while i > lo:
            t *= i * (m - k + i) / ((k - i + 1) * (n - i + 1))
            i -= 1
            tail += t
            if t < tail * 1e-16:
                break
    return min(1.0, 2 * tail)


def compare(feature: str, base_counts: Counter, window: list[str], alpha: float, min_psi: float) -> Check:
    """Is any one value now much more, or much less, common than in the baseline?

    Each value gets an exact two-sample test (hypergeometric: the baseline is a sample too), and the
    smallest p is multiplied by the number of values tested (Bonferroni), so a feature with many values
    is not more alarm-prone. drift-1.0 and 1.1 instead used a chi-square against baseline shares taken
    as exact; with a 1.4% value and a 60-line window it called 5 lines a near-certain change (decision 100).
    An alarm also needs a population stability index of at least min_psi over all values."""
    b, w = Counter(base_counts), Counter(window)
    m, n = sum(b.values()), len(window)
    if not m or not n:
        return Check(feature, n, 1.0, 0.0, False, [])
    cats = sorted(set(b) | set(w))
    ps = {c: hypergeom_two_sided(w[c], m, n, b[c] + w[c]) for c in cats}
    p = min(1.0, min(ps.values()) * len(cats))
    bp = {c: (b[c] + 0.5) / (m + 0.5 * len(cats)) for c in cats}
    wp = {c: (w[c] + 0.5) / (n + 0.5 * len(cats)) for c in cats}
    s_ = psi(bp, wp) if len(cats) > 1 else 0.0
    raw = sorted(cats, key=lambda k: (ps[k], -abs(w[k] / n - b[k] / m)))[:3]
    moved = [{"value": k, "baseline": round(b[k] / m, 4), "now": round(w[k] / n, 4)} for k in raw]
    return Check(feature, n, p, s_, p < alpha and s_ >= min_psi, moved)


# ------------------------------------------------------------------ streams

@dataclass(frozen=True)
class Event:
    day: date
    features: tuple   # ((name, value), ...): hashable, so streams can be compared in tests


def feats(e: Event) -> dict[str, str]:
    return dict(e.features)


@dataclass(frozen=True)
class Policy:
    """alpha, min_psi, window, min_window fixed before any result (decision 97). The baseline changed in
    1.1 (decision 99): 1.0 compared with the first 40% of history and alarmed in one healthy week in four
    on unseen worlds whose short-pay share grows over time. 1.1 compares with the 26 weeks before the window."""
    alpha: float = 1e-4          # per feature per weekly check
    min_psi: float = 0.10        # the usual "moderate shift" line
    window: int = 60             # most recent events compared
    min_window: int = 30         # no verdict on fewer
    baseline: str = "moving"     # "moving": the baseline_weeks before the window; "fixed": the first baseline_share
    #                              (1.2 chooses between them, and alpha, on development worlds: decision 100)
    baseline_weeks: int = 26
    baseline_share: float = 0.4  # fixed baseline only; also where checks start, for both kinds
    min_baseline: int = 60       # no verdict against a thinner baseline


CANDIDATES = {
    "moving_1e-4": Policy(baseline="moving", alpha=1e-4),
    "moving_1e-5": Policy(baseline="moving", alpha=1e-5),
    "fixed_1e-4": Policy(baseline="fixed", alpha=1e-4),
    "fixed_1e-5": Policy(baseline="fixed", alpha=1e-5),
}


def episodes(alarms: list["Alarm"], step_days: int = 7) -> int:
    """Alarms as production sees them: consecutive weekly alarms on the same feature are one alarm,
    because an open alarm stays open until a person clears it."""
    by: dict[str, list[date]] = {}
    for a in alarms:
        by.setdefault(a.check.feature, []).append(a.day)
    n = 0
    for ds in by.values():
        ds = sorted(set(ds))
        n += 1 + sum((ds[i] - ds[i - 1]).days > step_days for i in range(1, len(ds)))
    return n if alarms else 0


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
    """Weekly checks after the first baseline_share of history. Returns every alarm and the day checks
    start from. window overrides policy.window (price streams have many rows per week).

    Counts are kept per day, so a moving baseline is a sum of at most 26 weeks of small counters."""
    if not events:
        return [], None
    events = sorted(events, key=lambda e: e.day)
    days = sorted({e.day for e in events})
    base_end = days[int(len(days) * policy.baseline_share)]
    names = [k for k, _ in events[0].features]
    per_day: dict[date, dict[str, Counter]] = {}
    for e in events:
        d = per_day.setdefault(e.day, {f: Counter() for f in names})
        for f, v in e.features:
            d[f][v] += 1
    fixed = {f: sum((per_day[d][f] for d in days if d <= base_end), Counter()) for f in names}
    w = window or policy.window
    alarms = []
    later = [e for e in events if e.day > base_end]
    day, i = base_end + timedelta(days=step_days), 0
    while day <= days[-1] + timedelta(days=step_days - 1):
        while i < len(later) and later[i].day <= day:
            i += 1
        recent = later[max(0, i - w):i]
        if len(recent) >= policy.min_window:
            if policy.baseline == "fixed":
                base = fixed
            else:
                lo, hi = recent[0].day - timedelta(weeks=policy.baseline_weeks), recent[0].day
                ds = [d for d in days if lo <= d < hi]
                base = {f: sum((per_day[d][f] for d in ds), Counter()) for f in names}
            if sum(base[names[0]].values()) >= policy.min_baseline:
                for f in names:
                    c = compare(f, base[f], [feats(e)[f] for e in recent], policy.alpha, policy.min_psi)
                    if c.alarm:
                        alarms.append(Alarm(stream, day, c))
        day += timedelta(days=step_days)
    return alarms, base_end
