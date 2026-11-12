"""The forecast endpoints serve the published file as is: search, one series, and the summary."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cpgos.api import app as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    n, H = 3, 8
    f = tmp_path / "published.npz"
    rm = np.array([[0.9, 0.8], [0.7, 0.9], [np.nan, np.nan]], dtype=np.float32)    # columns: ses, lgbm
    np.savez_compressed(
        f, version=np.array("forecast-test"), gbm=np.array("gbm-x"), item=np.array(["FOODS_1", "FOODS_2", "HOBBIES_9"]),
        store=np.array(["CA_1", "CA_1", "TX_2"]), dept=np.array(["F1", "F1", "H1"]), cls=np.array(["smooth", "lumpy", "no_sales"]),
        weeks=np.array([f"2015-{1 + k // 4:02d}-0{1 + k % 4}" for k in range(60)]), future_weeks=np.array([f"2016-06-0{k + 1}" for k in range(H)]),
        history=np.ones((n, 52), np.float32), actual=np.full((n, H), np.nan, np.float32), price=np.ones((n, 60), np.float32),
        methods=np.array(["ses", "lgbm"]), test_fc=np.ones((n, 2, H), np.float32), test_rmsse=rm,
        test_lo=np.zeros((n, H), np.float32), test_hi=np.full((n, H), 2, np.float32), future=np.ones((n, H), np.float32),
        future_lo=np.zeros((n, H), np.float32), future_hi=np.full((n, H), 3, np.float32),
        dollars=np.array([500, 900, 0], np.float32))
    monkeypatch.setattr(api, "_PUBLISHED", f)
    api._forecasts.cache_clear()
    return TestClient(api.app)


def test_find_sorts_by_dollars_and_by_where_lightgbm_won_or_lost(client):
    assert [r["item"] for r in client.get("/api/forecast/find").json()] == ["FOODS_2", "FOODS_1", "HOBBIES_9"]
    assert client.get("/api/forecast/find?sort=win").json()[0]["item"] == "FOODS_1"        # 0.9 -> 0.8
    assert client.get("/api/forecast/find?sort=loss").json()[0]["item"] == "FOODS_2"       # 0.7 -> 0.9
    assert [r["item"] for r in client.get("/api/forecast/find?q=hob&store=TX_2").json()] == ["HOBBIES_9"]


def test_series_returns_every_part_and_missing_values_as_null(client):
    r = client.get("/api/forecast/series?item=FOODS_2&store=CA_1").json()
    assert set(r["test"]) == {"ses", "lgbm"} and len(r["future"]) == 8 and r["actual"][0] is None
    assert client.get("/api/forecast/series?item=NOPE&store=CA_1").status_code == 404


def test_summary_without_reports_still_answers(client):
    r = client.get("/api/forecast/summary").json()
    assert r["series"] == 3 and r["classes"]["lumpy"] == 1 and r["stores"] == ["CA_1", "TX_2"]
