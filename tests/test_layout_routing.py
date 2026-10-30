import json
import random

import numpy as np
import psycopg
import pytest
from PIL import Image, ImageDraw

from cpgos.documents import fatura as F
from cpgos.documents import layout as L

# Three drawn "layouts": boxes in different places; text-like noise varies per invoice.
LAYOUTS = {
    1: [(30, 30, 200, 90), (350, 40, 560, 70), (30, 500, 560, 700)],
    2: [(350, 30, 560, 120), (30, 200, 300, 260), (30, 600, 560, 800)],
    3: [(30, 300, 560, 360), (30, 40, 120, 120), (400, 700, 560, 800)],   # held back: never trained on
}


def draw(layout: int, instance: int) -> Image.Image:
    rng = random.Random(layout * 1000 + instance)
    im = Image.new("L", (595, 841), 255)
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in LAYOUTS[layout]:
        d.rectangle((x0, y0, x1, y1), outline=0, width=3)
        for _ in range(4):                                   # "words" that change per invoice
            yy = rng.randint(y0 + 6, max(y0 + 7, y1 - 10))
            d.line((x0 + 8, yy, x0 + 8 + rng.randint(20, (x1 - x0) // 2), yy), fill=60, width=2)
    return im


def _fixture(raw):
    """FATURA-shaped files: layouts 1-2 known (fit), layout 3 new (holdout), and an empty calibration
    layout 4 replaced by a small one, so the split rules match the real loader."""
    root = raw / "fatura" / "invoices_dataset_final"
    (root / "Annotations" / "Original_Format").mkdir(parents=True)
    (root / "images").mkdir()
    LAYOUTS[4] = [(200, 400, 400, 450), (30, 760, 560, 820)]
    rows = {"train": [], "dev": [], "test": []}
    for t in (1, 2, 3, 4):
        for i in range(12):
            key = f"Template{t}_Instance{i}"
            draw(t, i).save(root / "images" / f"{key}.jpg", quality=90)
            ann = {"DATE": {"bbox": [[300, 800], [450, 812]], "text": f"Date: {10 + i}-Mar-2008"}}
            (root / "Annotations" / "Original_Format" / f"{key}.json").write_text(json.dumps(ann))
            rows["train" if i < 8 else "dev" if i < 10 else "test"].append(key)
    for split, keys in rows.items():
        (root / f"strat1_{split}.csv").write_text("img_path,annot_path\n" + "".join(f"{k}.jpg,{k}.json\n" for k in keys))
    (root / "Strat2_Split.txt").write_text("train_inds = [1, 2, 4]\ndev_inds = [3]\ntest_inds = dev_inds\n")


@pytest.fixture
def loaded(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(F, "N_CALIBRATION_TEMPLATES", 1)
    monkeypatch.setattr(F, "CALIBRATION_SEED", 0)
    _fixture(tmp_path)
    F.load_fatura(conn, tmp_path, origin="test_fixture")
    calib = conn.execute("SELECT DISTINCT layout_id FROM eval.doc_meta WHERE split_unseen = 'calibration'").fetchone()[0]
    return tmp_path, calib


def test_signature_is_ink_density():
    white = Image.new("L", (595, 841), 255)
    black = Image.new("L", (595, 841), 0)
    assert L.signature(white, (12, 17)).max() == 0.0
    assert L.signature(black, (12, 17)).min() == 1.0
    assert L.signature(white, (12, 17)).shape == (12 * 17,)


def test_auroc_handles_ties_and_perfect_separation():
    assert L.auroc(np.array([3.0, 4.0]), np.array([1.0, 2.0])) == 1.0
    assert L.auroc(np.array([1.0]), np.array([1.0])) == 0.5
    assert L.auroc(np.array([]), np.array([1.0])) is None


def test_router_knows_trained_layouts_and_flags_new_ones(conn, loaded):
    raw, calib = loaded
    router, info = L.train(conn, raw)
    # Only the two fit layouts are learned: never the calibration layout, never the holdout.
    assert len(router.keys) == 2
    assert f"fatura_L{calib:02d}" not in router.keys and "fatura_L03" not in router.keys
    assert {c["grid"][0] for c in info["selection"]} == {g[0] for g in L.GRID_CANDIDATES}
    rep = L.evaluate(conn, raw, router)
    assert rep["known_layouts_clean"]["wrong_layout"] == 0
    assert rep["new_layouts_holdout"]["wrongly_matched_to_known"] == 0
    assert rep["new_layouts_holdout"]["called_new"] == rep["new_layouts_holdout"]["n"] == 12


def test_saved_router_round_trips_and_is_deduplicated(conn, loaded):
    raw, _ = loaded
    router, _ = L.train(conn, raw)
    a = L.save(conn, router)
    assert L.save(conn, router) == a                                 # same content -> same model row
    mid, back = L.load(conn)
    assert mid == a and back.keys == router.keys and back.grid == router.grid
    assert np.allclose(back.centroids, router.centroids, atol=1e-6)
    img = draw(1, 99)
    assert back.route(img)[0] == router.route(img)[0]


def test_routing_runs_as_engine_and_is_idempotent(conn, loaded):
    raw, _ = loaded
    L.save(conn, L.train(conn, raw)[0])
    first = L.route_documents(conn, raw)
    assert first["routed"] == 48 and first["known"] + first["new"] == 48
    assert L.route_documents(conn, raw)["routed"] == 0
    # Layout 3 (held back) is never routed to a known layout.
    leaked = conn.execute(
        """SELECT count(*) FROM document_route r JOIN eval.doc_meta m USING (source_document_id)
           WHERE m.layout_id = 3 AND r.layout_key IS NOT NULL""").fetchone()[0]
    assert leaked == 0


def test_engine_can_route_but_not_see_answers(conn, loaded):
    raw, _ = loaded
    L.save(conn, L.train(conn, raw)[0])
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        L.load(conn)                                                    # the trained router is readable
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM eval.doc_meta")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO layout_model (version, grid_w, grid_h, threshold, trained_on, content_sha256) "
                         "VALUES ('x', 1, 1, 1, '{}', repeat('a', 64))")


def test_perturbation_is_scan_like_but_bounded():
    img = draw(1, 0)
    p = L.perturb(img, random.Random(1))
    assert p.size == img.size and p.mode == "L"
    a, b = L.signature(img, (48, 68)), L.signature(p, (48, 68))
    assert 0 < np.abs(a - b).mean() < 0.2


def test_untrained_database_says_what_to_run(conn):
    with pytest.raises(LookupError, match="make train-router"):
        L.load(conn)
