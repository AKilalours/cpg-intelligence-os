"""One case as a picture: the evidence the agent could see, laid out on a time line.

Read with the agent's own role and tools, so the picture shows exactly what the agent saw (the damaged
evidence views, shelf prices, never units under the damage) and nothing it could not: a person looking
at the picture judges the same evidence the agent judged. Money stays as exact strings.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from cpgos.agent.tools import Tools, price_drop_weeks


def _iso(v):
    return v.isoformat() if isinstance(v, date) else v


def picture(conn: psycopg.Connection, case_id: int) -> dict | None:
    with conn.transaction():
        row = conn.execute("SELECT deduction_id FROM investigation_case WHERE case_id = %s", (case_id,)).fetchone()
        if row is None:
            return None
        did = row[0] if isinstance(row, tuple) else row["deduction_id"]
        conn.execute("SET LOCAL ROLE cpgos_agent")
        t = Tools(conn)
        c = t.claim(did)
        a = t.engine_assessment(did)
        p0, p1 = c["period_start"], c["period_end"]
        out = {"case_id": case_id, "claim": {k: _iso(v) if not hasattr(v, "as_tuple") else str(v) for k, v in c.items()},
               "money": {"claimed": str(c["amount"]), "supported": None if a["supported_amount"] is None else str(a["supported_amount"]),
                         "recoverable": None if a["recoverable_amount"] is None else str(a["recoverable_amount"])},
               "engine": {"verdict": a["verdict"], "rule": a["rule"]},
               "promotions": [], "prices": [], "drops": [], "pos": []}
        if p0 and p1 and c["sku_id"]:
            lo, hi = p0 - timedelta(weeks=8), p1 + timedelta(weeks=4)
            out["window"] = [lo.isoformat(), hi.isoformat()]
            out["promotions"] = [{"promotion_id": p["promotion_id"], "store": p["store_id"], "start": _iso(p["start_date"]),
                                  "end": _iso(p["end_date"]), "allowance": str(p["allowance_per_unit"]),
                                  "this_store": p["store_id"] == c["store_id"]}
                                 for p in t.promotions(c["partner_id"], c["sku_id"], lo, hi)]
            if c["store_id"]:
                prices = t.shelf_prices(c["store_id"], c["sku_id"], p0, hi)
                out["prices"] = [{"week": _iso(r["week"]), "price": float(r["shelf_price"])} for r in prices]
                out["drops"] = [d["week"] for d in price_drop_weeks(prices, p0, p1)]
                out["pos"] = [{"week": _iso(r["week_start"]), "units": None if r["units"] is None else float(r["units"])}
                              for r in t.pos_weeks(c["store_id"], c["sku_id"], lo, p1)]
        return out
