"""Weekly item-store demand from the real M5 daily sales: 30,490 series, most of them intermittent.

One row per (item, store), one column per full Monday-to-Sunday week. Units and the week's highest shelf
price are kept; a week a store did not stock an item (no price) is NaN, not zero, so "not on sale" is
never mistaken for "sold nothing". Built once with DuckDB and cached as a compressed numpy file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "data" / "processed" / "m5"
CACHE = ROOT / "data" / "processed" / "forecast" / "m5_weekly.npz"

HORIZON = 8            # weeks ahead: a brand's replenishment and promotion planning window
TEST_WEEKS = HORIZON   # the last 8 weeks are the test set, scored once per version
DEV_ORIGINS = 4        # rolling origins before the test, 8 weeks apart


@dataclass
class Weekly:
    units: np.ndarray        # (series, weeks) float, NaN where the item was not stocked
    price: np.ndarray        # (series, weeks) float, NaN where not stocked
    weeks: list[date]        # Monday of each week
    item: np.ndarray
    store: np.ndarray
    dept: np.ndarray
    cat: np.ndarray
    # Known in advance, so kept beyond the cut: the brand's planned shelf price (M5's weekly sell price) and
    # the calendar (event days by type, SNAP benefit days by state). Units after the cut are never kept.
    plan_price: np.ndarray | None = None      # (series, all weeks)
    events: np.ndarray | None = None          # (all weeks, 4): Sporting, Cultural, National, Religious days
    snap: np.ndarray | None = None            # (series, all weeks): SNAP days in the series' state

    def upto(self, t: int) -> "Weekly":
        """Only weeks before column t for sales and observed prices. The design code receives this, so the
        test weeks' sales are not there. The plan and calendar stay: they are known before the week."""
        return Weekly(self.units[:, :t].copy(), self.price[:, :t].copy(), self.weeks[:t],
                      self.item, self.store, self.dept, self.cat, self.plan_price, self.events, self.snap)

    @property
    def test_start(self) -> int:
        return len(self.weeks) - TEST_WEEKS

    def dev_origins(self) -> list[int]:
        """Column index of each development forecast origin (forecasts cover [o, o + HORIZON))."""
        return [self.test_start - HORIZON * k for k in range(DEV_ORIGINS, 0, -1)]


def build(parquet: Path = PARQUET, memory_limit: str = "2GB") -> Weekly:
    import duckdb
    db = duckdb.connect()
    db.execute(f"SET memory_limit = '{memory_limit}'")
    src = f"read_parquet('{str(parquet).rstrip('/')}/**/*.parquet', hive_partitioning=true)"
    first, last = db.execute(f"SELECT min(obs_date), max(obs_date) FROM {src}").fetchone()
    start = first + timedelta(days=(7 - first.weekday()) % 7)            # first Monday
    end = last - timedelta(days=(last.weekday() + 1) % 7)                # last Sunday
    n_weeks = ((end - start).days + 1) // 7
    rows = db.execute(f"""
        SELECT item_id, store_id, any_value(dept_id), any_value(cat_id),
               (date_diff('day', DATE '{start}', obs_date) // 7)::INT AS w,
               sum(units)::DOUBLE, max(sell_price)::DOUBLE, count(*)
        FROM {src} WHERE obs_date BETWEEN DATE '{start}' AND DATE '{end}'
        GROUP BY item_id, store_id, w""").fetchall()
    keys = sorted({(r[0], r[1]) for r in rows})
    idx = {k: i for i, k in enumerate(keys)}
    meta = {}
    units = np.full((len(keys), n_weeks), np.nan)
    price = np.full((len(keys), n_weeks), np.nan)
    for item, store, dept, cat, w, u, p, days in rows:
        i = idx[(item, store)]
        meta[i] = (dept, cat)
        if p is not None and days == 7:          # stocked the whole week: a real observation
            units[i, w], price[i, w] = u, p
    weeks = [start + timedelta(weeks=k) for k in range(n_weeks)]
    return Weekly(units, price, weeks, np.array([k[0] for k in keys]), np.array([k[1] for k in keys]),
                  np.array([meta[i][0] for i in range(len(keys))]), np.array([meta[i][1] for i in range(len(keys))]))


EVENT_TYPES = ("Sporting", "Cultural", "National", "Religious")


def calendar(weeks: list[date], store: np.ndarray, path: Path = ROOT / "data" / "raw" / "m5" / "calendar.csv"):
    """Event days by type per week, and SNAP days per week in each series' state (CA_1 -> CA)."""
    import csv
    idx = {w: i for i, w in enumerate(weeks)}
    ev = np.zeros((len(weeks), len(EVENT_TYPES)))
    snap_state = {s: np.zeros(len(weeks)) for s in ("CA", "TX", "WI")}
    with open(path) as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"])
            wk = idx.get(d - timedelta(days=d.weekday()))
            if wk is None:
                continue
            for col in ("event_type_1", "event_type_2"):
                if r[col] in EVENT_TYPES:
                    ev[wk, EVENT_TYPES.index(r[col])] += 1
            for s in snap_state:
                snap_state[s][wk] += int(r[f"snap_{s}"])
    snap = np.stack([snap_state[str(s)[:2]] for s in store])
    return ev, snap


def load(cache: Path = CACHE) -> Weekly:
    w = _load_sales(cache)
    w.plan_price = w.price
    w.events, w.snap = calendar(w.weeks, w.store)
    return w


def _load_sales(cache: Path) -> Weekly:
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        return Weekly(z["units"], z["price"], [date.fromisoformat(d) for d in z["weeks"]],
                      z["item"], z["store"], z["dept"], z["cat"])
    w = build()
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, units=w.units, price=w.price, weeks=np.array([d.isoformat() for d in w.weeks]),
                        item=w.item, store=w.store, dept=w.dept, cat=w.cat)
    return w
