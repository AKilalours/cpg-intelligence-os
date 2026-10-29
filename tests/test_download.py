import zipfile

import pytest

import cpgos.datasets.registry as reg
from scripts.download_data import fetch_http


@pytest.fixture
def spec(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "RAW_DIR", tmp_path / "raw")
    z = tmp_path / "src.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("calendar.csv", "date,wm_yr_wk\n")
        zf.writestr("extra.csv", "unused\n")
    s = reg.load_registry()["m5"]
    return reg.DatasetSpec(s.key, s.title, s.origin, s.method, s.license, s.license_verified, s.landing_page,
                           {**s.raw, "url": z.as_uri(), "files": ["calendar.csv"]})


def test_zip_extracts_only_listed_files(spec):
    files = fetch_http(spec)
    names = sorted(f.name for f in files)
    assert names == ["calendar.csv", "src.zip"]
    assert not (spec.raw_dir / "extra.csv").exists()
    assert not list(spec.raw_dir.glob("*.part"))  # no half-written files left behind


def test_zip_missing_expected_file_fails(spec):
    bad = reg.DatasetSpec(*[getattr(spec, f) for f in ("key", "title", "origin", "method", "license",
                                                       "license_verified", "landing_page")],
                          {**spec.raw, "files": ["sell_prices.csv"]})
    with pytest.raises(RuntimeError, match="missing expected files"):
        fetch_http(bad)
