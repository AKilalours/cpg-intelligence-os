"""Realistic changes to inject into a world's inputs, and the measurement of how fast monitors see them.

Each scenario changes the raw inputs from a start day on, then the real code runs on the changed
inputs (the cash engine re-matches every line), so the monitors see what production would see.

  po_numbers        the payer starts quoting its PO numbers instead of invoice numbers (some share)
  short_pay_wave    a wave of lines netted with a small shortage deduction
  partial_payments  a cash-strapped customer pays part of each invoice
  new_reason_words  deduction reasons worded in families the reader never saw
  price_war         a share of items repriced 20% down at the shelf
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import psycopg

from cpgos.codes import data as codes_data
from cpgos.codes.baselines import CharNaiveBayes
from cpgos.finance import cashapp
from cpgos.monitor import drift

HYBRID_THRESHOLD = 0.484      # the hybrid reader's Naive Bayes margin, chosen on its development set (decision 90)
WORLD_REASONS = {"WALMART PROMO SCANBACK": "scan_back", "SHORTAGE": "shortage", "LATE DELIVERY FEE": "late_fee",
                 "WALMART SPOILS": "spoils"}


@dataclass
class World:
    lines: list
    invoices: list
    deductions: list      # (day, reason text, code)
    prices: list          # (week, sku, store, price)


def load(conn: psycopg.Connection) -> World:
    lines, invoices = cashapp._load(conn, None, replay=True)
    ded = conn.execute("""SELECT d.deduction_date, d.raw_reason, c.code FROM deduction d
                          LEFT JOIN deduction_code c USING (deduction_code_id) ORDER BY d.deduction_date, d.deduction_id""").fetchall()
    prices = conn.execute("""SELECT s.obs_date, s.sku_id, s.store_id, s.shelf_price FROM sales_observation s
                             WHERE s.granularity = 'week' AND s.sku_id IN (SELECT DISTINCT sku_id FROM invoice_line)
                               AND s.store_id IN (SELECT DISTINCT store_id FROM invoice)""").fetchall()
    return World(list(lines), list(invoices), list(ded), list(prices))


_READER: CharNaiveBayes | None = None


def reader() -> CharNaiveBayes:
    global _READER
    if _READER is None:
        _READER = CharNaiveBayes([e for e in codes_data.generate() if e.split == "train"])
    return _READER


def streams(w: World, only: str | None = None) -> dict[str, list[drift.Event]]:
    out = {}
    if only in (None, "cash"):
        out["cash"] = drift.cash_events(w.lines, cashapp.match_lines(w.lines, w.invoices, "full"))
    if only in (None, "deductions"):
        out["deductions"] = drift.deduction_events(w.deductions, reader(), HYBRID_THRESHOLD)
    if only in (None, "prices"):
        out["prices"] = drift.price_events(w.prices)
    return out


# ------------------------------------------------------------------ changes

def po_numbers(w: World, start: date, share: float, rng: random.Random) -> World:
    lines = [(rl, d, net, f"PO{rng.randrange(10**6, 10**7)}" if d >= start and rng.random() < share else ref, p, g, dd, c)
             for rl, d, net, ref, p, g, dd, c in w.lines]
    return World(lines, w.invoices, w.deductions, w.prices)


def short_pay_wave(w: World, start: date, share: float, rng: random.Random) -> World:
    out = []
    for rl, d, net, ref, p, g, dd, c in w.lines:
        if d >= start and not dd and rng.random() < share:
            cut = (Decimal(g) * Decimal("0.03")).quantize(Decimal("0.01"))
            out.append((rl, d, Decimal(g) - cut, ref, p, g, cut, "SH"))
        else:
            out.append((rl, d, net, ref, p, g, dd, c))
    return World(out, w.invoices, w.deductions, w.prices)


def partial_payments(w: World, start: date, share: float, rng: random.Random) -> World:
    out = []
    for rl, d, net, ref, p, g, dd, c in w.lines:
        if d >= start and not dd and rng.random() < share:
            part = (Decimal(g) * Decimal(str(round(rng.uniform(0.5, 0.9), 2)))).quantize(Decimal("0.01"))
            out.append((rl, d, part, ref, p, part, dd, c))
        else:
            out.append((rl, d, net, ref, p, g, dd, c))
    return World(out, w.invoices, w.deductions, w.prices)


def new_reason_words(w: World, start: date, share: float, rng: random.Random, families: dict[str, list[str]]) -> World:
    """families: category -> phrasings the reader never trained on (set 1 to design, set 2 to score)."""
    out = []
    for d, text, code in w.deductions:
        cat = WORLD_REASONS.get(text)
        if d >= start and cat and rng.random() < share:
            text = codes_data._noisy(rng, rng.choice(families[cat]))
        out.append((d, text, code))
    return World(w.lines, w.invoices, out, w.prices)


def price_war(w: World, start: date, share: float, rng: random.Random) -> World:
    skus = sorted({s for _, s, _, _ in w.prices})
    hit = {s for s in skus if rng.random() < share}
    prices = [(wk, s, st, (Decimal(p) * Decimal("0.8")).quantize(Decimal("0.01")) if p is not None and wk >= start and s in hit else p)
              for wk, s, st, p in w.prices]
    return World(w.lines, w.invoices, w.deductions, prices)


SET1 = {cat: fams[1] for cat, fams in codes_data.FAMILIES.items()}   # development wordings
SET2 = codes_data.HELDOUT2                                         # scored-once wordings

SCENARIOS = {
    "po_numbers_25pct": ("cash", lambda w, s, r, fam: po_numbers(w, s, 0.25, r)),
    "short_pay_wave_30pct": ("cash", lambda w, s, r, fam: short_pay_wave(w, s, 0.30, r)),
    "partial_payments_30pct": ("cash", lambda w, s, r, fam: partial_payments(w, s, 0.30, r)),
    "new_reason_words_50pct": ("deductions", lambda w, s, r, fam: new_reason_words(w, s, 0.50, r, fam)),
    "price_war_30pct_of_items": ("prices", lambda w, s, r, fam: price_war(w, s, 0.30, r)),
}


def price_window(events: list[drift.Event], weeks: int = 4) -> int:
    per_week = len(events) / max(1, len({e.day for e in events}))
    return max(30, int(per_week * weeks))


def watch_all(st: dict[str, list[drift.Event]], policy: drift.Policy) -> dict[str, tuple]:
    return {name: drift.watch(name, ev, policy, window=price_window(ev) if name == "prices" else None)
            for name, ev in st.items()}


def false_alarms(st: dict[str, list[drift.Event]], policy: drift.Policy) -> dict:
    """Alarms on an unchanged history, per stream: every one is false."""
    out = {}
    for name, (alarms, base_end) in watch_all(st, policy).items():
        ev = st[name]
        days = (ev[-1].day - base_end).days if ev and base_end else 0
        weeks_alarming = len({a.day for a in alarms})
        out[name] = {"weeks_checked": days // 7, "weeks_with_an_alarm": weeks_alarming,
                     "first": None if not alarms else {"day": str(alarms[0].day), "feature": alarms[0].check.feature,
                                                       "moved": alarms[0].check.moved}}
    return out


def detection(w: World, scenario: str, policy: drift.Policy, rolls: int, families: dict, seed0: int = 0,
              horizon_days: int = 120) -> dict:
    """Each roll picks a start between 60% and 80% of the history and a fresh random draw. Detected: the
    stream alarms on or after the start, within horizon_days. Delay counts days and changed-stream events."""
    stream, change = SCENARIOS[scenario]
    base = streams(w, stream)
    days = sorted({e.day for e in base[stream]})
    if len(days) < 10:     # a world without this kind of data (say, no shelf prices): nothing to measure
        return {"stream": stream, "rolls": 0, "detected": 0, "no_data": True,
                "days_to_alarm": {"median": None, "p90": None}, "events_to_alarm": {"median": None, "p90": None},
                "rolls_with_an_alarm_before_the_change": 0}
    delays, events_after, detected, early = [], [], 0, 0
    for r in range(rolls):
        rng = random.Random(seed0 + r)
        start = days[int(len(days) * rng.uniform(0.6, 0.8))]
        st = streams(change(w, start, rng, families), stream)
        alarms, _ = watch_all({stream: st[stream]}, policy)[stream]
        early += any(a.day < start for a in alarms)
        hit = next((a for a in alarms if start <= a.day <= start + timedelta(days=horizon_days)), None)
        if hit:
            detected += 1
            delays.append((hit.day - start).days)
            events_after.append(sum(1 for e in st[stream] if start <= e.day <= hit.day))
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return {"stream": stream, "rolls": rolls, "detected": detected,
            "days_to_alarm": {"median": q(delays, 0.5), "p90": q(delays, 0.9)},
            "events_to_alarm": {"median": q(events_after, 0.5), "p90": q(events_after, 0.9)},
            "rolls_with_an_alarm_before_the_change": early}
