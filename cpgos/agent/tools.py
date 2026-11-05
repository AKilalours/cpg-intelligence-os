"""The agent's tools: typed, read-only questions about the ledger, each one logged.

Every query runs as cpgos_agent, which the database lets read only what a supplier would really have
(the damaged evidence views, invoices, terms, shelf prices) and never the truth under the damage
(migrations 0019 and 0020). The one write is arithmetic: a calculation row with its inputs, so every
number in a dispute can be recomputed. A tool never changes a ledger value; the role cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

TOOLS_VERSION = "agent-tools-1.0.0"
PRICE_DROP = Decimal("0.90")     # a shelf price at or below 90% of the prior 8-week high: the usual TPR signal
PRIOR_WEEKS = 8


@dataclass
class Tools:
    conn: psycopg.Connection
    log: list[dict] = field(default_factory=list)

    def _rows(self, name: str, sql: str, params: tuple) -> list[dict]:
        rows = self.conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()
        self.log.append({"tool": name, "params": [str(p) for p in params], "rows": len(rows)})
        return rows

    def claim(self, deduction_id: int) -> dict:
        r = self._rows("claim", """SELECT d.deduction_id, d.claim_number, d.partner_id, d.amount, d.deduction_date,
                                          d.dispute_deadline, d.raw_invoice_ref, d.sku_id, d.claimed_units,
                                          d.period_start, d.period_end, d.store_id, coalesce(c.code, '') AS code,
                                          d.raw_reason
                                   FROM observed.deduction d LEFT JOIN deduction_code c USING (deduction_code_id)
                                   WHERE d.deduction_id = %s""", (deduction_id,))
        return r[0]

    def engine_assessment(self, deduction_id: int) -> dict:
        return self._rows("engine_assessment", """SELECT verdict, rule, supported_amount, recoverable_amount, evidence,
                                                         engine_version
                                                  FROM deduction_assessment WHERE deduction_id = %s
                                                  ORDER BY assessment_id DESC LIMIT 1""", (deduction_id,))[0]

    def promotions(self, partner: int, sku: int, p0: date, p1: date, store: int | None = None) -> list[dict]:
        """Recorded promotions for this item overlapping the period (one store, or any store)."""
        return self._rows("promotions", """SELECT p.promotion_id, p.store_id, p.start_date, p.end_date, p.allowance_per_unit
                                           FROM observed.promotion p JOIN observed.promotion_sku ps USING (promotion_id)
                                           WHERE p.partner_id = %s AND ps.sku_id = %s AND p.start_date <= %s
                                             AND p.end_date >= %s AND (%s::bigint IS NULL OR p.store_id = %s)
                                           ORDER BY p.store_id, p.start_date""", (partner, sku, p1, p0, store, store))

    def pos_weeks(self, store: int, sku: int, p0: date, p1: date) -> list[dict]:
        """The retailer's POS as provided (gaps and restatements included)."""
        return self._rows("pos_weeks", """SELECT week_start, units FROM observed.retailer_pos_week
                                          WHERE store_id = %s AND sku_id = %s AND week_start BETWEEN %s AND %s
                                          ORDER BY week_start""", (store, sku, p0, p1))

    def shelf_prices(self, store: int, sku: int, p0: date, p1: date) -> list[dict]:
        """Weekly shelf prices (Walmart weeks), from 8 weeks before the period to its end. Prices only."""
        return self._rows("shelf_prices", """SELECT obs_date AS week, shelf_price FROM sales_observation
                                             WHERE granularity = 'week' AND store_id = %s AND sku_id = %s
                                               AND obs_date BETWEEN %s AND %s AND shelf_price IS NOT NULL
                                             ORDER BY obs_date""",
                          (store, sku, p0 - timedelta(days=7 * (PRIOR_WEEKS + 1)), p1))

    def accepted_against(self, promotion_ids: list[int], before_claim: str) -> Decimal:
        """Dollars the engine already accepted against these promotions on earlier claims."""
        if not promotion_ids:
            return Decimal(0)
        rows = self._rows("accepted_against", """
            WITH latest AS (SELECT DISTINCT ON (deduction_id) deduction_id, evidence, recoverable_amount
                            FROM deduction_assessment ORDER BY deduction_id, assessment_id DESC)
            SELECT coalesce(sum(d.amount - l.recoverable_amount), 0) AS taken
            FROM latest l JOIN observed.deduction d USING (deduction_id)
            WHERE d.claim_number < %s AND EXISTS (
                SELECT 1 FROM jsonb_array_elements(coalesce(l.evidence->'promotions', '[]')) e
                WHERE (e->>'promotion_id')::bigint = ANY(%s))""", (before_claim, promotion_ids))
        return Decimal(rows[0]["taken"])

    def record(self, kind: str, inputs: dict, result: Decimal) -> int:
        """Write the arithmetic behind a number, so a reviewer can recompute it."""
        cid = self.conn.execute(
            """INSERT INTO calculation (kind, formula_version, inputs, result, currency, computed_by)
               VALUES (%s, %s, %s, %s, 'USD', 'cpgos.agent.tools') RETURNING calculation_id""",
            (kind, TOOLS_VERSION, Jsonb(inputs), result)).fetchone()[0]
        self.log.append({"tool": "record", "kind": kind, "calculation_id": cid})
        return cid


def price_drop_weeks(prices: list[dict], p0: date, p1: date) -> list[dict]:
    """Weeks inside [p0, p1] priced at or below 90% of the highest price in the 8 weeks before. Pure."""
    out = []
    for i, w in enumerate(prices):
        if not (p0 - timedelta(days=6) <= w["week"] <= p1):
            continue
        prior = [Decimal(x["shelf_price"]) for x in prices[max(0, i - PRIOR_WEEKS):i]]
        if len(prior) >= 4 and Decimal(w["shelf_price"]) <= PRICE_DROP * max(prior):
            out.append({"week": str(w["week"]), "price": str(w["shelf_price"]), "prior_high": str(max(prior))})
    return out
