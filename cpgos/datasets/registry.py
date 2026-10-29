"""Dataset registry: what real data exists, its license, and pinned checksums.

Like a library checkout log: every file the system uses has a known origin,
a license someone agreed to, and a fingerprint (SHA-256) so you notice if the
book on the shelf was swapped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yaml"
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
LOCK_PATH = DATA_DIR / "datasets.lock.json"
ACCEPT_PATH = DATA_DIR / "license_acceptance.yaml"


class LicenseNotAcceptedError(PermissionError):
    pass


class ChecksumMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    title: str
    origin: str
    method: str
    license: str
    license_verified: bool
    landing_page: str
    raw: dict

    @property
    def raw_dir(self) -> Path:
        return RAW_DIR / self.key


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, DatasetSpec]:
    doc = yaml.safe_load(path.read_text())
    out: dict[str, DatasetSpec] = {}
    for key, d in doc["datasets"].items():
        out[key] = DatasetSpec(
            key=key,
            title=d["title"],
            origin=d["origin"],
            method=d["method"],
            license=d["license"],
            license_verified=bool(d["license_verified"]),
            landing_page=d["landing_page"],
            raw=d,
        )
    return out


def require_license_accepted(spec: DatasetSpec, accept_path: Path = ACCEPT_PATH) -> None:
    """Refuse to use data whose terms nobody has read and accepted."""
    accepted = {}
    if accept_path.exists():
        accepted = yaml.safe_load(accept_path.read_text()) or {}
    if accepted.get(spec.key) is not True:
        raise LicenseNotAcceptedError(
            f"{spec.key}: read the terms at {spec.landing_page} "
            f"({spec.license}), then add '{spec.key}: true' to {accept_path}."
        )


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def pin_or_verify(key: str, file_path: Path, lock_path: Path = LOCK_PATH) -> str:
    """Trust on first use, verify forever after.

    First time a file is seen, record its SHA-256. Every later run must match,
    otherwise results built on it are no longer reproducible.
    """
    digest = sha256_file(file_path)
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    entry = lock.setdefault(key, {})
    name = file_path.name
    if name in entry and entry[name] != digest:
        raise ChecksumMismatchError(
            f"{key}/{name} changed: pinned {entry[name][:12]}..., found {digest[:12]}.... "
            "If the upstream release changed on purpose, re-pin deliberately and re-run evals."
        )
    entry[name] = digest
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True))
    return digest
