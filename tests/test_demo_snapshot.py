"""The public demo's licence guard: research-only and registration-only datasets never leave this machine."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("demo_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "demo_snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)


def test_docile_and_registration_datasets_are_blocked():
    b = snap.blocked_keys()
    assert "docile" in b
    assert {"dunnhumby_breakfast", "dunnhumby_complete_journey"} <= b


def test_shareable_datasets_are_not_blocked():
    b = snap.blocked_keys()
    assert not {"fatura", "cord", "kehe_deduction_codes"} & b


def test_images_come_only_from_allow_listed_folders():
    assert snap.SHAREABLE_IMAGE_DIRS == ("fatura/", "cord/")
