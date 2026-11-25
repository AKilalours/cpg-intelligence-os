"""The recorder (scripts/public_snapshot.py) and the web reader (web/lib/snapshot.ts) must derive the same key
from the same request path; these cases are mirrored in web/lib/snapshot.test.ts."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("public_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "public_snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)


def test_keys_match_the_web_reader():
    assert snap.key("/api/forecast/series?item=FOODS_3_120&store=CA_3") == "_api_forecast_series_item_FOODS_3_120_store_CA_3"
    assert snap.key("/api/promo/evidence?dept=") == "_api_promo_evidence_dept_"
    assert snap.key("/api/lost/cell?store=TX_2&week=2016-02-29") == "_api_lost_cell_store_TX_2_week_2016_02_29"


def test_replay_grid_paths_match_javascript_number_formatting():
    # JavaScript writes 0 as "0" and 0.2 as "0.2"; the recorder must write the same text or the key differs.
    assert [f"{x:g}" for x in snap.FAULTS] == ["0", "0.05", "0.1", "0.2", "0.3"]
    assert [f"{x:g}" for x in snap.AUDITS] == ["0.02", "0.05", "0.1", "0.2", "0.33", "0.5"]


def test_values_are_encoded_like_encodeURIComponent():
    assert snap.q("FOODS_3") == "FOODS_3" and snap.q("2016-02-29") == "2016-02-29" and snap.q("a b/c") == "a%20b%2Fc"


def test_importing_the_recorder_does_not_switch_the_process_to_read_only():
    # Regression: the flag was set at import, so collecting this test file made every later write test get 403.
    import os
    assert os.environ.get("CPGOS_READ_ONLY") != "1"


def test_list_sizes_are_within_the_api_caps():
    # Regression: LIST_LIMIT was 200 while /api/forecast/find allows at most 100, so the recorder stopped with a 422.
    from cpgos.api import app as api
    route = next(r for r in api.app.routes if getattr(r, "path", "") == "/api/forecast/find")
    cap = next(p.field_info.metadata for p in route.dependant.query_params if p.name == "limit")
    le = next(m.le for m in cap if hasattr(m, "le"))
    assert snap.LIST_LIMIT <= le and snap.PROMO_LIST <= le
