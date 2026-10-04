"""Concurrency tests for the claim race gate.

Reproducibility strategy: contenders run in real threads but block on a
``DeterministicWriterLock`` until all of them have queued; the test then
admits them in a fixed, scripted order. The winner is fixed by the test, not
by scheduling. A second suite removes the lock entirely and relies solely on
the database compare-and-set ("equivalent constraint").
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.db import connect
from app.engines import projection, race_gate
from app.engines.write_lock import set_writer_lock, reset_writer_lock
from app.tests.deterministic import DeterministicWriterLock, NullWriterLock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TTL = 3600


def _open_wish(title: str = "并发愿望") -> int:
    c = connect()
    cur = c.execute(
        "INSERT INTO wishes(title,note,status,data_quality) VALUES (?,?,?,?)",
        (title, "", "open", "clean"),
    )
    c.commit()
    wid = cur.lastrowid
    c.close()
    return wid


def _row(wid: int) -> dict:
    c = connect()
    r = dict(c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone())
    c.close()
    return r


def _claim_in_thread(lock: DeterministicWriterLock, name: str, wid: int,
                     claim_at: datetime, box: dict) -> None:
    lock.declare(name)
    c = connect()
    try:
        box[name] = race_gate.claim_wish(c, wid, name, claim_at, TTL)
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _default_lock():
    yield
    reset_writer_lock()


def test_exactly_one_winner_with_fixed_outcome(db_dir):
    """Two open-race contenders: scripted order fixes alice as the winner."""
    wid = _open_wish()
    lock = DeterministicWriterLock()
    set_writer_lock(lock)
    names = ["alice", "bob"]
    lock.script(wid, names)

    results: dict = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(_claim_in_thread, lock, "alice", wid, NOW, results),
            pool.submit(_claim_in_thread, lock, "bob", wid, NOW + timedelta(seconds=10), results),
        ]
        lock.release_race(wid)
        for f in futs:
            f.result(timeout=5)

    alice, bob = results["alice"], results["bob"]
    assert alice.ok is True and alice.payload["claimer"] == "alice"
    # Stable loser contract: HTTP 409, machine-readable reason "locked".
    assert bob.ok is False
    assert bob.status_code == 409 and bob.reason == "locked"

    final = _row(wid)
    assert final["status"] == "claimed"
    assert final["claimer"] == "alice"
    # The loser must not rewrite the winner's lock: bob's clock is 10s later.
    assert final["claimed_at"] == alice.payload["claimed_at"]
    assert final["expires_at"] == alice.payload["expires_at"]


def test_loser_does_not_touch_winner_lock_even_when_admitted_second(db_dir):
    """Second contender reads the row only after the winner committed."""
    wid = _open_wish()
    lock = DeterministicWriterLock()
    set_writer_lock(lock)
    # Reverse the natural start order: bob queues first but must still lose.
    lock.script(wid, ["alice", "bob"])

    results: dict = {}
    t_bob = threading.Thread(target=_claim_in_thread,
                             args=(lock, "bob", wid, NOW + timedelta(seconds=30), results))
    t_alice = threading.Thread(target=_claim_in_thread,
                               args=(lock, "alice", wid, NOW, results))
    t_bob.start(); t_alice.start()
    lock.release_race(wid)
    t_bob.join(5); t_alice.join(5)

    assert results["alice"].ok is True
    assert results["bob"].reason == "locked"
    final = _row(wid)
    assert final["claimer"] == "alice"
    assert final["claimed_at"] == NOW.isoformat()


def test_three_views_pin_same_winner(db_dir):
    """Wall card, detail and my-claims carry the same winner pin."""
    wid = _open_wish("三路同钉")
    lock = DeterministicWriterLock()
    set_writer_lock(lock)
    names = ["alice", "bob", "carol"]
    lock.script(wid, names)

    results: dict = {}
    threads = [
        threading.Thread(target=_claim_in_thread,
                         args=(lock, n, wid, NOW + timedelta(seconds=i), results))
        for i, n in enumerate(names)
    ]
    for t in threads: t.start()
    lock.release_race(wid)
    for t in threads: t.join(5)

    assert [results[n].ok for n in names] == [True, False, False]
    assert {results[n].reason for n in names[1:]} == {"locked"}

    c = connect()
    wall = {p["id"]: p for p in projection.wall(c, NOW)}
    card = wall[wid]
    detail = projection.detail(c, wid, NOW)
    mine_alice = projection.mine(c, "alice", NOW)
    mine_bob = projection.mine(c, "bob", NOW)
    c.close()

    for view in (card, detail, mine_alice[0]):
        assert view["status"] == "claimed"
        assert view["claimer"] == "alice"
        assert view["claimed_at"] == NOW.isoformat()
        assert view["expires_at"] == (NOW + timedelta(seconds=TTL)).isoformat()
    assert mine_bob == []  # the loser is never pinned anywhere


def test_cas_alone_guarantees_no_double_claimed(db_dir):
    """Equivalent constraint: with NO writer lock, the CAS still allows one winner."""
    wid = _open_wish("无锁八连")
    set_writer_lock(NullWriterLock())
    names = [f"racer{i}" for i in range(8)]

    results: list = []
    barrier = threading.Barrier(len(names))

    def raid(name):
        c = connect()
        try:
            barrier.wait(5)
            results.append(race_gate.claim_wish(c, wid, name, NOW, TTL))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        for name in names:
            pool.submit(raid, name)

    winners = [r for r in results if r.ok]
    losers = [r for r in results if not r.ok]
    assert len(winners) == 1
    assert len(losers) == 7
    assert {r.reason for r in losers} == {"locked"}
    assert {r.status_code for r in losers} == {409}

    final = _row(wid)
    assert final["status"] == "claimed"
    assert final["claimer"] == winners[0].payload["claimer"]
    # Database-level invariant: exactly one claimer column, one claimed row.
    c = connect()
    n = c.execute(
        "SELECT COUNT(*) c FROM wishes WHERE id=? AND status='claimed' AND claimer IS NOT NULL",
        (wid,),
    ).fetchone()["c"]
    c.close()
    assert n == 1


def test_ttl_expired_reclaim_race_has_one_winner(db_dir):
    """An expired lock contested by two reclaimers is replaced exactly once."""
    c = connect()
    c.execute(
        "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,data_quality) "
        "VALUES (?,?,?,?,?,?,?)",
        ("过期锁", "", "claimed", "ghost",
         (NOW - timedelta(hours=2)).isoformat(),
         (NOW - timedelta(hours=1)).isoformat(), "clean"),
    )
    c.commit()
    wid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    c.close()

    lock = DeterministicWriterLock()
    set_writer_lock(lock)
    lock.script(wid, ["bob", "alice"])  # bob scripted to win
    results: dict = {}
    threads = [
        threading.Thread(target=_claim_in_thread, args=(lock, "alice", wid, NOW, results)),
        threading.Thread(target=_claim_in_thread, args=(lock, "bob", wid, NOW, results)),
    ]
    for t in threads: t.start()
    lock.release_race(wid)
    for t in threads: t.join(5)

    assert results["bob"].ok is True
    assert results["alice"].reason == "locked"
    final = _row(wid)
    assert final["claimer"] == "bob" and final["status"] == "claimed"


def test_fulfilled_wish_race_everyone_loses_stable_code(db_dir):
    c = connect()
    c.execute("INSERT INTO wishes(title,note,status,data_quality) VALUES (?,?,?,?)",
              ("已核销", "", "fulfilled", "clean"))
    c.commit()
    wid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    c.close()

    lock = DeterministicWriterLock()
    set_writer_lock(lock)
    lock.script(wid, ["alice", "bob"])
    results: dict = {}
    threads = [
        threading.Thread(target=_claim_in_thread, args=(lock, n, wid, NOW, results))
        for n in ("alice", "bob")
    ]
    for t in threads: t.start()
    lock.release_race(wid)
    for t in threads: t.join(5)

    assert {results["alice"].reason, results["bob"].reason} == {"already_fulfilled"}
    assert _row(wid)["claimer"] is None
