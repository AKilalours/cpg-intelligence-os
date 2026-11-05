"""Replays every value a person confirmed on FATURA invoices against the current routing rule.

No database, no model calls: the inputs the rule saw are in the fixture. Regenerate the fixture with
`make export-regressions` after reviewing. A failure here means a rule change would accept a value a
person already said is wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpgos.documents import regression as rg

FIXTURE = Path(__file__).parent / "fixtures" / "review_regressions.json"


def test_no_confirmed_value_is_broken_by_the_current_rule():
    cases = rg.load(FIXTURE)
    if not cases:
        pytest.skip("no exported reviews yet: review some invoices, then make export-regressions")
    out = rg.summary(rg.check(cases))
    assert out["regressions"] == 0, [w for w in out["wrong"] if w["kind"] == "regression"]
