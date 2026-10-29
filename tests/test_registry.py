import pytest

from cpgos.datasets.registry import (
    ChecksumMismatchError,
    LicenseNotAcceptedError,
    load_registry,
    pin_or_verify,
    require_license_accepted,
)


def test_registry_entries_are_complete():
    reg = load_registry()
    assert {"m5", "docile", "fatura", "dunnhumby_breakfast", "open_food_facts"} <= set(reg)
    for spec in reg.values():
        assert spec.landing_page.startswith("https://")
        assert spec.origin in {"real", "real_template_generated"}
        assert spec.method in {"http", "kaggle", "manual", "bundled"}


def test_license_must_be_accepted(tmp_path):
    spec = load_registry()["m5"]
    accept = tmp_path / "accept.yaml"
    with pytest.raises(LicenseNotAcceptedError):
        require_license_accepted(spec, accept)
    accept.write_text("m5: true\n")
    require_license_accepted(spec, accept)


def test_checksum_pins_then_detects_change(tmp_path):
    f, lock = tmp_path / "data.csv", tmp_path / "lock.json"
    f.write_text("a,b\n1,2\n")
    pin_or_verify("x", f, lock)
    pin_or_verify("x", f, lock)  # same file: fine
    f.write_text("a,b\n1,3\n")
    with pytest.raises(ChecksumMismatchError):
        pin_or_verify("x", f, lock)
