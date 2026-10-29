"""Load small published reference tables (e.g. KeHE deduction code prefixes)."""

from __future__ import annotations

import csv
from pathlib import Path

import psycopg

from cpgos.datasets.registry import sha256_file

KEHE_CODES = Path(__file__).resolve().parents[1] / "datasets" / "kehe_deduction_codes.csv"


def load_deduction_codes(conn: psycopg.Connection, path: Path = KEHE_CODES, partner_name: str = "KeHE") -> int:
    with conn.transaction():
        conn.execute(
            """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
               VALUES ('kehe_deduction_codes', %s, %s, 'public supplier documentation, per-row source', 'real')
               ON CONFLICT (dataset_key, sha256) DO NOTHING""",
            (path.name, sha256_file(path)),
        )
        partner_id = conn.execute(
            """INSERT INTO trading_partner (name, partner_type) VALUES (%s, 'distributor')
               ON CONFLICT (name, partner_type) DO UPDATE SET name = EXCLUDED.name
               RETURNING partner_id""",
            (partner_name,),
        ).fetchone()[0]
        n = 0
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                conn.execute(
                    """INSERT INTO deduction_code (partner_id, code, description, code_family, source_url)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (partner_id, code) DO UPDATE
                         SET description = EXCLUDED.description,
                             code_family = EXCLUDED.code_family,
                             source_url = EXCLUDED.source_url""",
                    (partner_id, r["code"], r["description"], r["code_family"], r["source_url"]),
                )
                n += 1
    return n
