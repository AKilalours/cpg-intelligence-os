"""Drift alarms in the database: raised by the monitor role, cleared only by a named person with a reason.

watch(conn) runs the monitors on the live data and raises an alarm for each feature whose latest weekly
check fails; while a cash alarm is open, the gate (stp_route) sends every would-be automatic posting to
a person. clear() is a person saying "I looked; this change is understood" (for example, "the payer
switched remittance software on 3 March; references checked by hand").
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cpgos.monitor import drift, scenarios


class AlarmError(ValueError):
    pass


def rebaseline_points(conn: psycopg.Connection) -> dict[str, object]:
    """For each stream, the data date of its most recently cleared alarm. A person clearing an alarm says
    the change is understood, so the new normal starts there: the baseline is rebuilt from data after it.
    Without this, a fixed baseline still holds the old format and the same alarm returns next week."""
    rows = conn.execute("""SELECT stream, max(checked_through) FROM drift_alarm WHERE cleared_at IS NOT NULL
                           GROUP BY stream""").fetchall()
    return {r[0]: r[1] for r in rows}


def current_checks(conn: psycopg.Connection, policy: drift.Policy = drift.SHIPPED) -> list[drift.Alarm]:
    """Failing checks at the most recent weekly check of each stream (not the whole history), with each
    stream's baseline starting after its last cleared alarm."""
    st = scenarios.streams(scenarios.load(conn))
    for name, since in rebaseline_points(conn).items():
        if name in st:
            st[name] = [e for e in st[name] if e.day > since]
    out = []
    for name, (alarms, _) in scenarios.watch_all(st, policy).items():
        if alarms and st[name]:
            last_check = max(a.day for a in alarms)
            latest_day = max(e.day for e in st[name])
            if (latest_day - last_check).days < 7:
                out += [a for a in alarms if a.day == last_check]
    return out


def raise_alarms(conn: psycopg.Connection, found: list[drift.Alarm]) -> list[int]:
    ids = []
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_monitor")
        for a in found:
            row = conn.execute(
                """INSERT INTO drift_alarm (stream, feature, checked_through, p_value, psi, moved, monitor_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (stream, feature) WHERE cleared_at IS NULL DO NOTHING
                   RETURNING drift_alarm_id""",
                (a.stream, a.check.feature, a.day, a.check.p_value, a.check.psi, Jsonb(a.check.moved),
                 drift.DRIFT_VERSION)).fetchone()
            if row:
                ids.append(row[0])
                conn.execute("INSERT INTO audit_event (actor_type, actor_id, action, payload) VALUES ('system', %s, 'drift_alarm', %s)",
                             (drift.DRIFT_VERSION, Jsonb({"drift_alarm_id": row[0], "stream": a.stream, "feature": a.check.feature,
                                                          "p_value": a.check.p_value, "psi": a.check.psi, "moved": a.check.moved})))
    return ids


def watch(conn: psycopg.Connection, policy: drift.Policy = drift.SHIPPED) -> list[int]:
    return raise_alarms(conn, current_checks(conn, policy))


def clear(conn: psycopg.Connection, alarm_id: int, reviewer: str, note: str) -> dict:
    who = (reviewer or "").strip()
    if not who:
        raise AlarmError("a reviewer name is required: clearing an alarm is attributed")
    if not note or not note.strip():
        raise AlarmError("say what was checked: clearing an alarm lets the engine post on its own again")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        row = conn.execute("SELECT stream, feature, cleared_at FROM drift_alarm WHERE drift_alarm_id = %s",
                           (alarm_id,)).fetchone()
        if row is None:
            raise AlarmError(f"no alarm {alarm_id}")
        if row[2] is not None:
            raise AlarmError("this alarm was already cleared")
        conn.execute("UPDATE drift_alarm SET cleared_at = clock_timestamp(), cleared_by = %s, clear_note = %s "
                     "WHERE drift_alarm_id = %s", (who, note.strip(), alarm_id))
        conn.execute("INSERT INTO audit_event (actor_type, actor_id, action, payload) VALUES ('human', %s, 'drift_clear', %s)",
                     (who, Jsonb({"drift_alarm_id": alarm_id, "stream": row[0], "feature": row[1], "note": note.strip()})))
    return {"drift_alarm_id": alarm_id, "cleared_by": who}


def open_alarms(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        """SELECT drift_alarm_id, stream, feature, checked_through, p_value, psi, moved, monitor_version, raised_at
           FROM drift_alarm WHERE cleared_at IS NULL ORDER BY raised_at""").fetchall()
