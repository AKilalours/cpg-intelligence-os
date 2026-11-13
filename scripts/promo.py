"""Addition 10: can promotion lift be predicted? Scored once on the test weeks' real price cuts.

    python scripts/promo.py score     # once per version (writes reports/promo-1.0.0_test.json)
    python scripts/promo.py publish   # the evidence the planner shows (data/processed, not in git)

Three ways to say what a cut will do, each judged on the controlled lift of every real price cut in the
test weeks (decision 107):
  zero      - assume no lift
  history   - median controlled lift of earlier cuts in the same department and depth band
  model     - the shipped LightGBM: forecast with the cut price over forecast with the regular price
Error: absolute error on lift clipped to [-100%, +300%] (a few cuts sold 50 times their normal week).
Rule written before scoring: the planner shows the model's lift only if its error is lower than "zero"
with a paired 95% bootstrap interval excluding zero; it shows the history estimate on the same terms;
otherwise it shows the evidence and the break-even arithmetic alone.
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

from cpgos.forecast import data, evidence, gbm, promo  # noqa: E402

VERSION = "promo-1.0.0"
REPORT = ROOT / "reports" / f"{VERSION}_test.json"
EVIDENCE = ROOT / "data" / "processed" / "forecast" / "promo_events.npz"


def _boot(a: np.ndarray, b: np.ndarray, n: int = 4000, seed: int = 0) -> dict:
    """Mean of a minus b with a 95% paired bootstrap interval (negative favours a)."""
    rng = np.random.default_rng(seed)
    d = a - b
    bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)])
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return {"difference": round(float(d.mean()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)]}


def cmd_score() -> int:
    if REPORT.exists():
        print(f"already scored ({REPORT.relative_to(ROOT)}); scored once per version")
        return 0
    t0 = time.time()
    w = data.load()
    T = w.test_start
    ev = evidence.build(w)
    test = ev["week"] >= T
    hist = dict((k, v) for k, v in ev.items())
    table = evidence.dept_band_median(hist, w.dept, ~test)
    s, t, depth, actual = ev["series"][test], ev["week"][test], ev["depth"][test], ev["lift"][test]
    model = gbm.train(w.upto(T), T)
    pred_model = promo.predicted_lifts(model, w.upto(T), T, list(zip(s.tolist(), t.tolist())))
    bands = evidence.band(depth)
    pred_hist = np.array([table.get((str(w.dept[i]), int(b)), 0.0) for i, b in zip(s, bands)])
    clip = lambda x: np.clip(x, -1, 3)
    ok = np.isfinite(pred_model)
    err = {"zero": np.abs(clip(actual) - 0), "history": np.abs(clip(actual) - clip(pred_hist)),
           "model": np.abs(clip(actual) - clip(np.nan_to_num(pred_model)))}
    body = {"version": VERSION, "gbm": gbm.VERSION, "test_cuts": int(test.sum()), "model_scored": int(ok.sum()),
            "mae": {k: round(float(v[ok].mean()), 4) for k, v in err.items()},
            "model_vs_zero": _boot(err["model"][ok], err["zero"][ok]),
            "history_vs_zero": _boot(err["history"][ok], err["zero"][ok]),
            "median_actual": round(float(np.median(actual)), 4), "median_model": round(float(np.nanmedian(pred_model)), 4),
            "median_history": round(float(np.median(pred_hist)), 4),
            "share_actual_positive": round(float((actual > 0).mean()), 4)}
    body["show_model"] = body["model_vs_zero"]["ci95"][1] < 0
    body["show_history"] = body["history_vs_zero"]["ci95"][1] < 0
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(body, indent=1))
    print(json.dumps(body, indent=1))
    print(f"saved {REPORT.relative_to(ROOT)}  {time.time() - t0:.0f}s")
    return 0


def cmd_publish() -> int:
    w = data.load()
    ev = evidence.build(w)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(EVIDENCE, dept=w.dept[ev["series"]], item=w.item[ev["series"]], store=w.store[ev["series"]],
                        week=np.array([w.weeks[t].isoformat() for t in ev["week"]]),
                        **{k: np.asarray(ev[k], dtype=np.float32) for k in ("depth", "lift", "regular", "units", "baseline")},
                        temporary=ev["temporary"])
    print(f"published {len(ev['lift'])} controlled price cuts to {EVIDENCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["score", "publish"])
    sys.exit({"score": cmd_score, "publish": cmd_publish}[ap.parse_args().command]())
