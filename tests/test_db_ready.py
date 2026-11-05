"""Waiting for Postgres: an open port or a starting server is not a ready database."""

from __future__ import annotations

import psycopg
import pytest

from cpgos.db import DatabaseNotReady, wait_until_ready


class Fake:
    """A server that refuses the first `refusals` connections, as Postgres does while starting."""

    def __init__(self, refusals):
        self.refusals, self.calls = refusals, 0

    def __call__(self, url, connect_timeout):
        self.calls += 1
        if self.calls <= self.refusals:
            raise psycopg.OperationalError("FATAL:  the database system is starting up")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        assert sql == "SELECT 1"


def _clock():
    t = [0.0]
    return (lambda: t[0]), (lambda dt: t.__setitem__(0, t[0] + dt))


def test_waits_through_start_up_then_returns():
    clock, sleep = _clock()
    fake = Fake(refusals=4)
    assert wait_until_ready("x", timeout=30, connect=fake, sleep=sleep, clock=clock) == 5


def test_gives_up_at_the_deadline_with_what_to_do():
    clock, sleep = _clock()
    with pytest.raises(DatabaseNotReady, match=r"after 5s \(FATAL:  the database system is starting up\).*make db"):
        wait_until_ready("x", timeout=5, interval=1, connect=Fake(refusals=10**6), sleep=sleep, clock=clock)


def test_a_real_server_is_ready_at_once(db_url):
    assert wait_until_ready(db_url, timeout=5) == 1
