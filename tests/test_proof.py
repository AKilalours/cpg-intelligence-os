"""The proof wall reads every number from stored reports; a missing report means a missing card, never a
made-up one."""

from __future__ import annotations

import json

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cpgos.api import app as api


def test_cards_come_only_from_reports_that_exist(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_REPORTS", tmp_path)
    c = conn.cursor(row_factory=dict_row)
    assert api.proof_cards(c.connection) == []
    conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('agent_seeds', 'x', %s)", (Jsonb({
        "worlds": 8, "all_regions": {"engine": {"false_disputes": {"k": 24}}, "agent": {"false_disputes": {"k": 1}},
                                     "paired": {"false_disputes": {"p_value": 2e-6}}}}),))
    (tmp_path / "promo-1.0.0_test.json").write_text(json.dumps({"test_cuts": 167, "mae": {"zero": 0.99, "model": 1.04}}))
    conn.row_factory = dict_row
    cards = {k["key"]: k for k in api.proof_cards(conn)}
    assert set(cards) == {"agent", "promo"}
    assert cards["agent"]["visual"] == {"type": "dots", "left": 24, "left_label": "rules", "right": 1,
                                        "right_label": "agent", "p": 2e-6}
    assert cards["promo"]["visual"]["honest_no"] is True
