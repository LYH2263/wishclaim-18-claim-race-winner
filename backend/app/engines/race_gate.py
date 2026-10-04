"""Race gate: the single testable critical section for claiming.

Two concurrent requests on one open wish must end with exactly one claimer.
This module collapses the read-decide-write sequence:

1. a per-wish writer lock (``write_lock``) serializes in-process writers;
2. a compare-and-set UPDATE with a claimability predicate makes the write
   itself win/lose atomically, so even an out-of-process contender can never
   produce a double ``claimed`` row;
3. the loser performs no write: it never mutates the row nor the winner's
   lock, and returns a stable error reason.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app.engines.claim_lock import claim_allowed, lock_payload, release_if_expired
from app.engines.write_lock import get_writer_lock

_COLUMNS = "id, title, note, status, claimer, claimed_at, expires_at, data_quality"

# Stable loser codes (kept identical to the pre-race API).
NOT_FOUND = "not_found"
LOCKED = "locked"
ALREADY_FULFILLED = "already_fulfilled"
BAD_STATUS = "bad_status"
NOT_CLAIMED = "not_claimed"
NEED_CLAIM = "need_claim"


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    status_code: int = 200
    payload: dict | None = None


def _fetch(c: sqlite3.Connection, wish_id: int) -> sqlite3.Row | None:
    return c.execute(f"SELECT {_COLUMNS} FROM wishes WHERE id=?", (wish_id,)).fetchone()


def _expire_inside_lock(c: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> sqlite3.Row:
    """Sweep one stale lock while holding its writer lock.

    The identity predicate (same claimer + expires_at we read) guarantees we
    never clear a lock that was concurrently replaced.
    """
    rel = release_if_expired(row["status"], row["expires_at"], now)
    if not rel:
        return row
    cur = c.execute(
        "UPDATE wishes SET status='open', claimer=NULL, claimed_at=NULL, expires_at=NULL "
        "WHERE id=? AND status='claimed' AND claimer IS ? AND expires_at IS ?",
        (row["id"], row["claimer"], row["expires_at"]),
    )
    c.commit()
    if cur.rowcount == 0:
        # Another writer replaced the expired lock before us; re-read it.
        fresh = _fetch(c, row["id"])
        if fresh is not None:
            return fresh
    return _fetch(c, row["id"])


def claim_wish(c: sqlite3.Connection, wish_id: int, claimer: str,
               now: datetime, ttl_seconds: int) -> GateResult:
    """Claim one wish. Exactly one concurrent caller returns ok=True."""
    with get_writer_lock().for_wish(wish_id):
        row = _fetch(c, wish_id)
        if row is None:
            return GateResult(False, NOT_FOUND, 404)
        row = _expire_inside_lock(c, row, now)

        decision = claim_allowed(row["status"], row["claimer"], now, row["expires_at"])
        if not decision["ok"]:
            # Loser path: read-only, the winner's lock is untouched.
            return GateResult(False, decision["reason"], 409)

        payload = lock_payload(claimer, now, ttl_seconds)
        # Compare-and-set: the predicate is re-checked by the database at
        # write time. A second writer that already flipped the row to a live
        # claimed lock makes this UPDATE match zero rows -> stable loss.
        cur = c.execute(
            "UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=? "
            "WHERE id=? AND ("
            "  status IN ('open','released')"
            "  OR (status='claimed' AND expires_at IS NOT NULL AND expires_at <= ?)"
            ")",
            (payload["status"], payload["claimer"], payload["claimed_at"],
             payload["expires_at"], wish_id, now.isoformat()),
        )
        if cur.rowcount == 0:
            return GateResult(False, LOCKED, 409)
        c.commit()
        return GateResult(True, "", 200, payload)


def release_wish(c: sqlite3.Connection, wish_id: int) -> GateResult:
    with get_writer_lock().for_wish(wish_id):
        row = _fetch(c, wish_id)
        if row is None:
            return GateResult(False, NOT_FOUND, 404)
        if row["status"] != "claimed":
            return GateResult(False, NOT_CLAIMED, 400)
        cur = c.execute(
            "UPDATE wishes SET status='released', claimer=NULL, claimed_at=NULL, expires_at=NULL "
            "WHERE id=? AND status='claimed' AND claimer IS ? AND expires_at IS ?",
            (wish_id, row["claimer"], row["expires_at"]),
        )
        if cur.rowcount == 0:
            return GateResult(False, NOT_CLAIMED, 400)
        c.commit()
        return GateResult(True, "", 200, {"ok": True, "status": "released"})


def fulfill_wish(c: sqlite3.Connection, wish_id: int) -> GateResult:
    with get_writer_lock().for_wish(wish_id):
        row = _fetch(c, wish_id)
        if row is None:
            return GateResult(False, NOT_FOUND, 404)
        if row["status"] != "claimed":
            return GateResult(False, NEED_CLAIM, 400)
        cur = c.execute(
            "UPDATE wishes SET status='fulfilled' WHERE id=? AND status='claimed'",
            (wish_id,),
        )
        if cur.rowcount == 0:
            return GateResult(False, NEED_CLAIM, 400)
        c.commit()
        return GateResult(True, "", 200, {"ok": True, "status": "fulfilled"})


def sweep_expired(c: sqlite3.Connection, now: datetime) -> list[int]:
    """Release every stale lock, each inside its own writer lock."""
    ids = [r["id"] for r in c.execute("SELECT id FROM wishes WHERE status='claimed'")]
    released: list[int] = []
    for wish_id in ids:
        with get_writer_lock().for_wish(wish_id):
            row = _fetch(c, wish_id)
            if row is None:
                continue
            if release_if_expired(row["status"], row["expires_at"], now):
                cur = c.execute(
                    "UPDATE wishes SET status='open', claimer=NULL, claimed_at=NULL, expires_at=NULL "
                    "WHERE id=? AND status='claimed' AND claimer IS ? AND expires_at IS ?",
                    (wish_id, row["claimer"], row["expires_at"]),
                )
                if cur.rowcount:
                    released.append(wish_id)
    c.commit()
    return released
