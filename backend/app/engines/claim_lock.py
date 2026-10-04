"""写锁 write lock: claim mutex + TTL release for wishes.

`claim_critical_section` is the only place a claim is written. The whole
read-modify-write runs inside one BEGIN IMMEDIATE transaction, and the
UPDATE re-checks the race gate's SQL predicate, so exactly one concurrent
claimer can win a wish — the loser's rowcount is 0 and the lock is untouched.
"""
from datetime import datetime, timedelta, timezone

def parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def claim_allowed(status: str, claimer: str | None, now: datetime, expires_at: str | None) -> dict:
    """Back-compat wrapper: the race gate owns the decision logic."""
    from app.engines.race_gate import evaluate
    r = evaluate(status, claimer, expires_at, now)
    return {"ok": r["ok"], "reason": r["code"]}

def lock_payload(claimer: str, now: datetime, ttl_seconds: int) -> dict:
    exp = now + timedelta(seconds=ttl_seconds)
    return {
        "status": "claimed",
        "claimer": claimer,
        "claimed_at": now.isoformat(),
        "expires_at": exp.isoformat(),
    }

def release_if_expired(status: str, expires_at: str | None, now: datetime) -> dict | None:
    if status != "claimed" or not expires_at:
        return None
    if parse_ts(expires_at) <= now:
        return {"status": "open", "claimer": None, "claimed_at": None, "expires_at": None}
    return None

def claim_critical_section(conn, wish_id: int, claimer: str, now: datetime,
                           ttl_seconds: int, before_write=None) -> dict:
    """Atomic claim: exactly one concurrent caller wins; losers change nothing.

    BEGIN IMMEDIATE takes the database write lock up front, serializing
    concurrent claimers; the conditional UPDATE then re-asserts the gate
    predicate so the row cannot be claimed twice even if the transaction
    model changes. Returns {"ok", "code", "payload"} — codes are stable
    (see race_gate). `before_write` is a test-only hook used to fix the
    winner deterministically in race tests.
    """
    from app.engines.race_gate import CLAIM_LOST, SQL_PREDICATE, evaluate
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT * FROM wishes WHERE id=?", (wish_id,)).fetchone()
        if r is None:
            conn.rollback()
            return {"ok": False, "code": "not_found"}
        gate = evaluate(r["status"], r["claimer"], r["expires_at"], now)
        if not gate["ok"]:
            conn.rollback()
            return {"ok": False, "code": gate["code"]}
        p = lock_payload(claimer, now, ttl_seconds)
        if before_write:
            before_write()
        cur = conn.execute(
            "UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=? "
            f"WHERE id=? AND {SQL_PREDICATE}",
            (p["status"], p["claimer"], p["claimed_at"], p["expires_at"],
             wish_id, now.isoformat()),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return {"ok": False, "code": CLAIM_LOST}
        conn.commit()
        return {"ok": True, "code": "", "payload": p}
    except Exception:
        conn.rollback()
        raise
