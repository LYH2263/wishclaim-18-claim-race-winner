"""竞态门禁 race gate: pure claim decision + the SQL predicate that mirrors it.

The gate is the single source of truth for "may this claim attempt proceed".
`evaluate` is the pure, testable half; `SQL_PREDICATE` is the same condition
expressed as a WHERE fragment so the write lock can enforce it atomically
inside the critical section. Keep the two in lockstep — test_race_gate.py
proves they agree on a state matrix.

Loser codes are stable: a rejected attempt always maps to one of
LOCKED / ALREADY_FULFILLED / BAD_STATUS / CLAIM_LOST, never a timing-dependent
value, so clients can key off them.
"""
from app.engines.claim_lock import parse_ts

LOCKED = "locked"
ALREADY_FULFILLED = "already_fulfilled"
BAD_STATUS = "bad_status"
CLAIM_LOST = "claim_lost"  # row moved between gate and write; defensive fallback

# Mirrors evaluate(): a row is claimable iff it is open/released, or its
# claim lock has expired. Timestamps are ISO-8601 UTC, so lexicographic
# compare is chronological. Parameter: now.isoformat().
SQL_PREDICATE = (
    "(status IN ('open','released') OR "
    "(status='claimed' AND claimer IS NOT NULL AND expires_at IS NOT NULL "
    "AND expires_at <= ?))"
)

def evaluate(status: str, claimer: str | None, expires_at: str | None, now) -> dict:
    """Pure gate. ok=True means the attempt may proceed to the write lock."""
    if status == "fulfilled":
        return {"ok": False, "code": ALREADY_FULFILLED}
    if status == "claimed" and claimer:
        if expires_at and parse_ts(expires_at) <= now:
            return {"ok": True, "code": "ttl_expired_reclaim"}
        return {"ok": False, "code": LOCKED}
    if status in ("open", "released"):
        return {"ok": True, "code": ""}
    return {"ok": False, "code": BAD_STATUS}
