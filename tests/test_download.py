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


def test_huggingface_files_are_verified_against_published_checksums(tmp_path, monkeypatch):
    import hashlib
    import io
    import json as _json
    import scripts.download_data as dd
    from cpgos.datasets.registry import DatasetSpec
    good, bad = b"parquet-bytes", b"tampered"
    listing = [{"type": "file", "path": "data/train-00000.parquet", "size": len(good),
                "lfs": {"oid": hashlib.sha256(good).hexdigest()}}, {"type": "directory", "path": "data/x"}]
    served = {"payload": good}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(url, timeout):
        return R(_json.dumps(listing).encode() if "/api/" in url else served["payload"])
    spec = DatasetSpec("cord", "t", "real", "huggingface", "l", True, "x", {"repo": "a/b", "path": "data"})
    monkeypatch.setattr(DatasetSpec, "raw_dir", property(lambda self: tmp_path / self.key))
    files = dd.fetch_huggingface(spec, opener)
    assert [f.name for f in files] == ["train-00000.parquet"] and files[0].read_bytes() == good
    assert dd.fetch_huggingface(spec, opener) == files                     # already verified: no re-download
    files[0].unlink()
    served["payload"] = bad
    import pytest
    with pytest.raises(RuntimeError, match="checksum does not match"):
        dd.fetch_huggingface(spec, opener)
    assert not (tmp_path / "cord" / "data" / "train-00000.parquet").exists()
    assert not list((tmp_path / "cord" / "data").glob("*.part"))
