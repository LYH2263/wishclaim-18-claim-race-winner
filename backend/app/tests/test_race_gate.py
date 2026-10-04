"""Race gate: stable codes, and the SQL predicate must mirror evaluate()."""
from datetime import datetime, timedelta, timezone

from app.db import connect
from app.engines.race_gate import (
    ALREADY_FULFILLED, BAD_STATUS, LOCKED, SQL_PREDICATE, evaluate,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(hours=1)).isoformat()
PAST = (NOW - timedelta(minutes=1)).isoformat()

def test_open_and_released_are_claimable():
    assert evaluate("open", None, None, NOW)["ok"] is True
    assert evaluate("released", None, None, NOW)["ok"] is True

def test_locked_code_is_stable_for_live_lock():
    r = evaluate("claimed", "alice", FUTURE, NOW)
    assert r["ok"] is False and r["code"] == LOCKED

def test_expired_lock_is_reclaimable():
    assert evaluate("claimed", "alice", PAST, NOW)["ok"] is True

def test_fulfilled_and_garbage_are_rejected_with_stable_codes():
    assert evaluate("fulfilled", "alice", FUTURE, NOW)["code"] == ALREADY_FULFILLED
    assert evaluate("archived", None, None, NOW)["code"] == BAD_STATUS
    assert evaluate("claimed", None, None, NOW)["code"] == BAD_STATUS

# (status, claimer, expires_at) — covers every gate branch.
STATES = [
    ("open", None, None),
    ("released", None, None),
    ("claimed", "alice", FUTURE),
    ("claimed", "alice", PAST),
    ("claimed", "alice", None),
    ("claimed", None, FUTURE),
    ("fulfilled", "alice", FUTURE),
    ("archived", None, None),
]

def test_sql_predicate_mirrors_gate(temp_db):
    """The write lock's WHERE clause must agree with the pure gate."""
    c = connect()
    try:
        for status, claimer, exp in STATES:
            cur = c.execute(
                "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,data_quality)"
                " VALUES (?,?,?,?,?,?,?)",
                ("s", "", status, claimer, None, exp, "clean"))
            wid = cur.lastrowid
            n = c.execute(
                f"SELECT COUNT(*) k FROM wishes WHERE id=? AND {SQL_PREDICATE}",
                (wid, NOW.isoformat())).fetchone()["k"]
            sql_ok = n == 1
            py_ok = evaluate(status, claimer, exp, NOW)["ok"]
            assert sql_ok == py_ok, (status, claimer, exp)
    finally:
        c.close()
