"""Lost-sales detection on real M5 sales (Founding Staff project 4: "detecting anomalies in sales data").

    python scripts/anomaly.py design   # 24 development weeks before the test: choose the detector
    python scripts/anomaly.py score    # once per version: the chosen detector on the 8 test weeks
    python scripts/anomaly.py publish  # real flagged weeks and estimated lost sales, for the app

Rule written before any result (decision 115): each detector's threshold flags 1% of ordinary development
weeks; the detector that catches the most planted stock-outs at that rate ships (ties to the simpler one).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cpgos.forecast import anomaly as an, data  # noqa: E402

VERSION = "anomaly-1.0.0"
FLAG_RATE = 0.01
ORDER = ("zero_rule", "poisson", "neg_binomial")          # simplest first, for ties
REPORT = ROOT / "reports" / f"{VERSION}_test.json"
DESIGN = ROOT / "local_runs" / f"{VERSION}_design.json"
PUBLISHED = ROOT / "data" / "processed" / "forecast" / "lost_sales.npz"


def evaluate(w: data.Weekly, weeks: range, seed: int, thresholds: dict | None = None) -> dict:
    stocked = ~np.isnan(w.price)
    clean = an.score_weeks(w.units, stocked, weeks)
    th = thresholds or {k: an.threshold_for(FLAG_RATE, clean[k]) for k in ORDER}
    U, planted = an.inject(w.units, w.price, weeks, 0.3, np.random.default_rng(seed))
    dirty = an.score_weeks(U, stocked, weeks)
    P = planted[:, weeks.start:weeks.stop]
    full = P & (U[:, weeks.start:weeks.stop] == 0)
    mu = np.stack([an.expected(w.units, t) for t in weeks], axis=1)
    # Dollars at stake in each planted week: expected units times shelf price (reported, not used to choose).
    at_stake = np.nan_to_num(mu * w.price[:, weeks.start:weeks.stop]) * P
    out = {"weeks": [str(w.weeks[weeks.start]), str(w.weeks[weeks.stop - 1])], "planted": int(P.sum()),
           "thresholds": th, "detectors": {}}
    for k in ORDER:
        flag_clean = np.nan_to_num(clean[k], nan=-1) > th[k]
        flag_dirty = np.nan_to_num(dirty[k], nan=-1) > th[k]
        bands = {}
        for name, lo, hi in (("1-3 a week", 1, 3), ("3-10 a week", 3, 10), ("10+ a week", 10, 1e9)):
            m = P & (mu >= lo) & (mu < hi)
            bands[name] = {"planted": int(m.sum()), "caught": round(float(flag_dirty[m].mean()), 4) if m.any() else None}
        out["detectors"][k] = {
            "flag_rate_on_real_weeks": round(float(flag_clean[~np.isnan(clean[k])].mean()), 4),
            "caught": round(float(flag_dirty[P].mean()), 4),
            "caught_dollar_weighted": round(float((at_stake * flag_dirty).sum() / at_stake.sum()), 4) if at_stake.sum() else None,
            "caught_full_stockouts": round(float(flag_dirty[full].mean()), 4),
            "caught_partial": round(float(flag_dirty[P & ~full].mean()), 4),
            "by_speed": bands}
    return out


def _show(r: dict) -> None:
    print(f"   weeks {r['weeks'][0]} to {r['weeks'][1]}, {r['planted']:,} planted stock-out weeks")
    for k, d in r["detectors"].items():
        b = " | ".join(f"{n}: {v['caught']:.0%}" for n, v in d["by_speed"].items() if v["caught"] is not None)
        print(f"   {k:<13} flags {d['flag_rate_on_real_weeks']:.1%} of real weeks; catches {d['caught']:.1%} "
              f"({d['caught_dollar_weighted']:.1%} of dollars at stake) "
              f"(full {d['caught_full_stockouts']:.1%}, partial {d['caught_partial']:.1%}) | {b}")


def choose(r: dict) -> str:
    best = max(r["detectors"][k]["caught"] for k in ORDER)
    return next(k for k in ORDER if r["detectors"][k]["caught"] >= best - 1e-9)


def cmd_design() -> int:
    t0 = time.time()
    w = data.load()
    T = w.test_start
    dev = w.upto(T)
    r = evaluate(dev, range(T - 24, T), seed=1)
    r["chosen"] = choose(r)
    DESIGN.parent.mkdir(exist_ok=True)
    DESIGN.write_text(json.dumps(r, indent=1))
    print(f"{VERSION} design (development weeks only, test weeks cut off):")
    _show(r)
    print(f"   chosen by the rule written beforehand: {r['chosen']}   ({time.time() - t0:.0f}s)")
    return 0


def cmd_score() -> int:
    if REPORT.exists():
        print(f"already scored ({REPORT.relative_to(ROOT)}); the test weeks are scored once per version")
        return 0
    if not DESIGN.exists():
        print("run design first: the detector and its threshold are chosen on development weeks")
        return 1
    d = json.loads(DESIGN.read_text())
    w = data.load()
    T = w.test_start
    r = evaluate(w, range(T, T + data.HORIZON), seed=2, thresholds=d["thresholds"])
    r.update({"version": VERSION, "chosen": d["chosen"], "chosen_in_design": d["detectors"][d["chosen"]],
              "flag_rate_target": FLAG_RATE})
    REPORT.write_text(json.dumps(r, indent=1))
    print(f"{VERSION} TEST (scored once; thresholds from design):")
    _show(r)
    print(f"   shipped: {d['chosen']}   saved {REPORT.relative_to(ROOT)}")
    return 0


def cmd_publish() -> int:
    """Real weeks the shipped detector flags over the last 52 weeks of data, with estimated lost sales:
    expected units minus sold, at the week's shelf price. An estimate: it assumes normal sales would have
    happened, and some flags will be data problems rather than empty shelves."""
    d = json.loads(DESIGN.read_text())
    k, th = d["chosen"], d["thresholds"][d["chosen"]]
    w = data.load()
    T = w.units.shape[1]
    weeks = range(T - 52, T)
    stocked = ~np.isnan(w.price)
    s = an.score_weeks(w.units, stocked, weeks)[k]
    mu = np.stack([an.expected(w.units, t) for t in weeks], axis=1)
    U, Pr = w.units[:, weeks.start:], w.price[:, weeks.start:]
    flag = np.nan_to_num(s, nan=-1) > th
    lost_units = np.where(flag, np.maximum(mu - U, 0), 0)
    lost = np.nan_to_num(lost_units * Pr)
    ii, jj = np.where(flag)
    np.savez_compressed(PUBLISHED, item=w.item[ii], store=w.store[ii], dept=w.dept[ii],
                        week=np.array([w.weeks[weeks.start + j].isoformat() for j in jj]),
                        sold=U[ii, jj].astype(np.float32), expected=mu[ii, jj].astype(np.float32),
                        price=Pr[ii, jj].astype(np.float32), lost=lost[ii, jj].astype(np.float32),
                        surprise=s[ii, jj].astype(np.float32), detector=np.array(k), threshold=np.array(th))
    print(f"published {len(ii):,} flagged weeks over 52 weeks; estimated lost sales ${lost.sum():,.0f} "
          f"(of ${np.nansum(U * Pr):,.0f} sold)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["design", "score", "publish"])
    return {"design": cmd_design, "score": cmd_score, "publish": cmd_publish}[ap.parse_args().command]()


if __name__ == "__main__":
    sys.exit(main())
