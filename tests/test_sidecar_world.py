import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sidecar", Path(__file__).parents[1] / "scripts" / "sidecar_world.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def test_the_side_database_is_next_to_the_main_one_and_never_the_main_one():
    side, admin, name = sc.sibling("postgresql://u:secret@localhost:5432/cpgos?sslmode=disable")
    assert name == "cpgos_world12" and side.endswith("/cpgos_world12?sslmode=disable") and "secret" in side
    assert admin.endswith("/postgres?sslmode=disable")
    with pytest.raises(ValueError):
        sc.sibling("postgresql://u@localhost:5432/")
    with pytest.raises(ValueError):
        sc.sibling("postgresql://u@localhost/cpgos", suffix="")
