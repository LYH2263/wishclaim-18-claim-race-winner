"""并发胜出钉: exactly one claimer wins an open wish; the loser gets a
stable code and the lock is never modified by a losing attempt.

Winner/loser are fixed reproducibly: sequentially (same-thread order), by an
ordering hook (threaded but deterministic), and by a barrier free-for-all
that asserts the invariant regardless of scheduling.
"""
import threading
from datetime import datetime, timedelta, timezone

from app.db import connect
from app.engines.claim_lock import claim_critical_section
from app.engines.race_gate import ALREADY_FULFILLED, LOCKED

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TTL = 3600

def add_wish(status="open", claimer=None, claimed_at=None, expires_at=None):
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,data_quality)"
            " VALUES (?,?,?,?,?,?,?)",
            ("race", "", status, claimer, claimed_at, expires_at, "clean"))
        return cur.lastrowid
    finally:
        c.close()

def lock_row(wid):
    c = connect()
    try:
        r = c.execute(
            "SELECT status,claimer,claimed_at,expires_at FROM wishes WHERE id=?",
            (wid,)).fetchone()
        return dict(r)
    finally:
        c.close()

def run_claim(wid, name, results, key, barrier=None, wait=None, before_write=None):
    try:
        c = connect()
        try:
            if wait:
                wait()
            if barrier:
                barrier.wait()
            results[key] = claim_critical_section(c, wid, name, NOW, TTL,
                                                  before_write=before_write)
        finally:
            c.close()
    except Exception as e:  # surface thread failures as a result, not silence
        results[key] = {"ok": False, "code": f"exc:{e}"}

def test_sequential_fixed_winner_loser_keeps_lock(temp_db):
    wid = add_wish()
    c = connect()
    try:
        first = claim_critical_section(c, wid, "alice", NOW, TTL)
        assert first["ok"] is True and first["payload"]["claimer"] == "alice"
        before = lock_row(wid)
        second = claim_critical_section(c, wid, "bob", NOW + timedelta(seconds=1), TTL)
        assert second["ok"] is False and second["code"] == LOCKED
        assert lock_row(wid) == before  # 败方不改锁
    finally:
        c.close()

def test_threaded_fixed_winner_via_ordering_hook(temp_db):
    """bob's BEGIN IMMEDIATE collides with alice's held write lock — real
    contention, but alice's win is fixed by the before_write hook."""
    wid = add_wish()
    alice_inside_write = threading.Event()
    results = {}
    ta = threading.Thread(target=run_claim,
                          args=(wid, "alice", results, "a"),
                          kwargs={"before_write": alice_inside_write.set})
    tb = threading.Thread(target=run_claim,
                          args=(wid, "bob", results, "b"),
                          kwargs={"wait": lambda: alice_inside_write.wait(5)})
    ta.start(); tb.start(); ta.join(15); tb.join(15)
    assert results["a"]["ok"] is True
    assert results["b"] == {"ok": False, "code": LOCKED}
    row = lock_row(wid)
    assert row["status"] == "claimed" and row["claimer"] == "alice"

def test_free_for_all_exactly_one_winner(temp_db):
    """8 contenders released on a barrier, 3 rounds: exactly one ok, every
    loser gets the same stable code, and the wish is never double-claimed."""
    for _ in range(3):
        wid = add_wish()
        n = 8
        barrier = threading.Barrier(n)
        results = {}
        threads = [threading.Thread(target=run_claim,
                                    args=(wid, f"c{i}", results, i),
                                    kwargs={"barrier": barrier})
                   for i in range(n)]
        for t in threads: t.start()
        for t in threads: t.join(15)
        oks = [r for r in results.values() if r["ok"]]
        losses = [r for r in results.values() if not r["ok"]]
        assert len(oks) == 1, results
        assert len(losses) == n - 1
        assert {r["code"] for r in losses} == {LOCKED}  # 败方错误码稳定
        row = lock_row(wid)
        assert row["status"] == "claimed"
        assert row["claimer"] == oks[0]["payload"]["claimer"]  # 禁止双 claimed

def test_expired_lock_reclaim_race_still_single_winner(temp_db):
    wid = add_wish(status="claimed", claimer="ghost",
                   claimed_at=(NOW - timedelta(hours=2)).isoformat(),
                   expires_at=(NOW - timedelta(hours=1)).isoformat())
    barrier = threading.Barrier(2)
    results = {}
    threads = [threading.Thread(target=run_claim,
                                args=(wid, name, results, name),
                                kwargs={"barrier": barrier})
               for name in ("alice", "bob")]
    for t in threads: t.start()
    for t in threads: t.join(15)
    oks = [r for r in results.values() if r["ok"]]
    assert len(oks) == 1
    assert lock_row(wid)["claimer"] == oks[0]["payload"]["claimer"]

def test_fulfilled_rejects_all_contenders(temp_db):
    wid = add_wish(status="fulfilled", claimer="alice",
                   claimed_at=NOW.isoformat(), expires_at=None)
    c = connect()
    try:
        for name in ("bob", "carol"):
            r = claim_critical_section(c, wid, name, NOW, TTL)
            assert r["ok"] is False and r["code"] == ALREADY_FULFILLED
    finally:
        c.close()
