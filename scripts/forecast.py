"""Phase 5a: demand forecasting on the real M5 weekly series (30,490 item-store series).

    python scripts/forecast.py design   # 4 development origins only; the test weeks are cut off first
    python scripts/forecast.py score    # once per version: the frozen shipped model on the test weeks

The design run never holds the test weeks (data.Weekly.upto). Per-series best-fit is chosen on dev origins
1-3 and judged on origin 4, so the selection itself is tested before it is used on the test weeks.
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

from cpgos.forecast import backtest as bt, data  # noqa: E402

VERSION = "forecast-1.0.0"
OUT = ROOT / "local_runs"


def _row(name, s):
    bc = " ".join(f"{c} {v:.3f}" for c, v in s["by_class"].items() if v is not None)
    return f"   {name:<16} WRMSSE {s['wrmsse']:.4f}  mean RMSSE {s['mean_rmsse']:.4f}  MASE {s['mase']:.3f}  bias {s['bias']:+.3f}  | {bc}"


CACHE = OUT / "forecast_cache"


def _dev_view():
    full = data.load()
    w = full.upto(full.test_start)                      # the test weeks are gone from here on
    origins = [w.units.shape[1] - data.HORIZON * k for k in range(data.DEV_ORIGINS, 0, -1)]
    return w, origins


def cmd_gbm_origin(i: int) -> int:
    """Train and forecast one dev origin, cached (lets a short-lived shell build the design run in steps)."""
    from cpgos.forecast import gbm
    t = time.time()
    w, origins = _dev_view()
    o = origins[i]
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(CACHE / f"lgbm_{gbm.VERSION}_{w.weeks[o]}.npy", gbm.lgbm(w.upto(o), o))
    print(f"lgbm origin {w.weeks[o]} cached  {time.time() - t:.0f}s")
    return 0


def cmd_design(no_gbm: bool) -> int:
    t0 = time.time()
    w, origins = _dev_view()
    print(f"{VERSION} design: {w.units.shape[0]} series, history to {w.weeks[-1]}, dev origins "
          f"{[str(w.weeks[o]) for o in origins]}", flush=True)
    errors, scores = [], {}
    names = None
    for i, o in enumerate(origins):
        fcs = bt.forecasts_at(w, o, with_gbm=not no_gbm, cache_dir=CACHE)
        names = names or list(fcs)
        errors.append(bt.per_series_rmsse(w, o, fcs))
        if i == len(origins) - 1:
            # Selection strategies, all chosen on origins 1-3 and judged here on origin 4.
            from cpgos.forecast import metrics as mt
            train, price = w.units[:, :o], w.price[:, :o]
            cls, wts = mt.demand_class(train), mt.weights(train, price)
            mean_w = {k: np.nanmean([np.nansum(wts * np.nan_to_num(e[k])) / np.sum(wts * ~np.isnan(e[k]))
                                     for e in errors[:-1]]) for k in names}
            single = min(mean_w, key=mean_w.get)
            strategies = {"best_fit": bt.best_fit(errors[:-1], names),
                          "best_by_class": bt.best_by_class(errors[:-1], names, cls, wts),
                          "best_with_margin": bt.best_with_margin(errors[:-1], names, single)}
            for sname, choice in strategies.items():
                fcs[sname] = bt.pick(fcs, names, choice)
                scores.setdefault("choice_share", {})[sname] = {k: round(float((choice == j).mean()), 4)
                                                                for j, k in enumerate(names) if (choice == j).any()}
            scores["single_best_on_origins_1_3"] = single
        scores[str(w.weeks[o])] = bt.score_all(w, o, fcs)
        print(f" origin {w.weeks[o]}", flush=True)
        for k, s in scores[str(w.weeks[o])].items():
            print(_row(k, s), flush=True)
    last = str(w.weeks[origins[-1]])
    single = scores["single_best_on_origins_1_3"]
    print(f"\n judged on {last} (never used to choose):")
    rivals = ["seasonal_naive", single] + [k for k in ("combo",) if k in scores[last]] + list(scores["choice_share"])
    for k in rivals:
        print(_row(k + (" (single best)" if k == single else ""), scores[last][k]))
    for k, v in scores["choice_share"].items():
        print(f"   {k} picks: {v}")
    OUT.mkdir(exist_ok=True)
    (OUT / "forecast_design.json").write_text(json.dumps({"version": VERSION, "scores": scores}, indent=1))
    print(f"saved local_runs/forecast_design.json  {time.time() - t0:.0f}s")
    return 0


# Frozen before the test was touched (decision 103): plain LightGBM (gbm-1.2.0) ships. On dev origin 4,
# best-with-margin was 0.2% better, but its paired 95% interval against plain LightGBM included zero
# (-0.8% to +0.2%), and the rule was: when the best strategy is not proven better than the single global
# model, ship the simpler one. The test reports every method, with paired intervals for the shipped model
# against exponential smoothing and against seasonal naive.
SHIPPED = "lgbm"
REPORT = ROOT / "reports" / f"{VERSION}_test.json"


def cmd_score() -> int:
    from cpgos.forecast import compare, gbm, metrics
    if REPORT.exists():
        print(f"already scored ({REPORT.relative_to(ROOT)}); the test weeks are scored once per version")
        return 0
    t = time.time()
    full = data.load()
    o = full.test_start
    hist = full.upto(o)
    fcs = {name: f(hist.units, data.HORIZON) for name, f in __import__("cpgos.forecast.models", fromlist=["LOCAL"]).LOCAL.items()}
    fcs["lgbm"] = gbm.lgbm(hist, o)
    fcs["combo"] = np.mean([fcs[k] for k in bt.COMBO], axis=0)
    train, actual = full.units[:, :o], full.units[:, o:o + data.HORIZON]
    wts, cls, sc = metrics.weights(train, full.price[:, :o]), metrics.demand_class(train), metrics.scale(train)
    scores = {k: metrics.summary(actual, f, train, wts, cls) for k, f in fcs.items()}
    r = {k: metrics.rmsse(actual, f, sc) for k, f in fcs.items()}
    paired = {f"{SHIPPED}_vs_{k}": compare.paired_bootstrap(r[SHIPPED], r[k], wts) for k in ("ses", "seasonal_naive", "tsb", "combo")}
    classes = {c: int((cls == c).sum()) for c in metrics.CLASSES}
    body = {"version": VERSION, "gbm": gbm.VERSION, "shipped": SHIPPED, "test_weeks": [str(full.weeks[o]), str(full.weeks[-1])],
            "series": int(full.units.shape[0]), "series_by_class": classes, "scores": scores, "paired": paired}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(body, indent=1, default=float))
    print(f"{VERSION} TEST (scored once): {full.weeks[o]} to {full.weeks[-1]}, {full.units.shape[0]} series {classes}")
    for k in ("seasonal_naive", "naive", "ma13", "ses", "croston", "sba", "tsb", "combo", "lgbm"):
        print(_row(k + (" (shipped)" if k == SHIPPED else ""), scores[k]))
    for k, v in paired.items():
        print(f"   {k}: {v}")
    print(f"saved {REPORT.relative_to(ROOT)}  {time.time() - t:.0f}s")
    return 0


PUBLISHED = ROOT / "data" / "processed" / "forecast" / "published.npz"


def cmd_publish() -> int:
    """What the app shows: per series, a year of history, the test weeks with every method against what
    sold, and the next 8 weeks with an 80% range. The range is fitted on development origins only, then its
    coverage measured on the test weeks (a second, different use of them, decided before it was computed)."""
    from cpgos.forecast import gbm, intervals, metrics, models
    t = time.time()
    full = data.load()
    o, T = full.test_start, full.units.shape[1]
    dev = full.upto(o)
    origins = [o - data.HORIZON * k for k in range(data.DEV_ORIGINS, 0, -1)]
    pairs = []
    for d in origins:
        fc = np.load(CACHE / f"lgbm_{gbm.VERSION}_{dev.weeks[d]}.npy")
        tr = dev.units[:, :d]
        pairs.append((dev.units[:, d:d + data.HORIZON], fc, metrics.scale(tr), metrics.demand_class(tr)))
    table = intervals.fit(pairs)
    hist = full.upto(o)
    test_fc = {name: f(hist.units, data.HORIZON) for name, f in models.LOCAL.items()}
    test_fc["lgbm"] = gbm.lgbm(hist, o)
    train = full.units[:, :o]
    sc, cls = metrics.scale(train), metrics.demand_class(train)
    lo, hi = intervals.apply(test_fc["lgbm"], sc, cls, table)
    actual = full.units[:, o:]
    cov = intervals.coverage(actual, lo, hi)
    cov_cls = {c: intervals.coverage(actual[cls == c], lo[cls == c], hi[cls == c]) for c in metrics.CLASSES}
    rm = {k: metrics.rmsse(actual, f, sc) for k, f in test_fc.items()}
    # The future: trained on everything, forecasting the 8 weeks after the data ends. The calendar is known
    # for those weeks; the planned price is not in M5 beyond the data, so it is left empty there.
    from datetime import timedelta
    future_weeks = [full.weeks[-1] + timedelta(weeks=k) for k in range(1, data.HORIZON + 1)]
    # Calendar for the future weeks from M5's calendar file where it reaches (to 19 June 2016), else zero.
    ev_all, snap_all = data.calendar(full.weeks + future_weeks, full.store)
    ext = data.Weekly(full.units, full.price, full.weeks, full.item, full.store, full.dept, full.cat,
                      np.concatenate([full.price, np.full((len(full.item), data.HORIZON), np.nan)], axis=1),
                      ev_all, snap_all)
    fut = gbm.lgbm(ext, T)
    sc_all, cls_all = metrics.scale(full.units), metrics.demand_class(full.units)
    flo, fhi = intervals.apply(fut, sc_all, cls_all, table)
    H0 = o - 52
    f32 = lambda a: np.asarray(a, dtype=np.float32)
    PUBLISHED.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        PUBLISHED, version=np.array(VERSION), gbm=np.array(gbm.VERSION), item=full.item, store=full.store,
        dept=full.dept, cls=cls, cls_recent=metrics.demand_class(train[:, -52:]), weeks=np.array([d.isoformat() for d in full.weeks[H0:]]),
        future_weeks=np.array([d.isoformat() for d in future_weeks]),
        history=f32(full.units[:, H0:o]), actual=f32(actual), price=f32(full.price[:, H0:]),
        methods=np.array(list(test_fc)), test_fc=f32(np.stack([test_fc[k] for k in test_fc], axis=1)),
        test_rmsse=f32(np.stack([rm[k] for k in test_fc], axis=1)), test_lo=f32(lo), test_hi=f32(hi),
        future=f32(fut), future_lo=f32(flo), future_hi=f32(fhi),
        dollars=f32(np.nansum(full.units[:, o - 52:o] * full.price[:, o - 52:o], axis=1)))
    summary = {"version": VERSION, "interval": "80% (10th to 90th percentile of development errors)",
               "test_coverage": round(cov, 4), "test_coverage_by_class": {k: round(v, 4) for k, v in cov_cls.items()},
               "future_weeks": [str(future_weeks[0]), str(future_weeks[-1])]}
    (ROOT / "reports" / f"{VERSION}_intervals.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"published {PUBLISHED.relative_to(ROOT)} ({PUBLISHED.stat().st_size / 1e6:.1f} MB)  {time.time() - t:.0f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["design", "gbm-origin", "score", "publish"])
    ap.add_argument("--no-gbm", action="store_true")
    ap.add_argument("--origin", type=int, default=0)
    a = ap.parse_args()
    if a.command == "score":
        return cmd_score()
    if a.command == "publish":
        return cmd_publish()
    return cmd_gbm_origin(a.origin) if a.command == "gbm-origin" else cmd_design(a.no_gbm)


if __name__ == "__main__":
    sys.exit(main())
