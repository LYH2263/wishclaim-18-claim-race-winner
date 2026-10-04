"""End-to-end race tests through the FastAPI stack.

The writer lock is removed here on purpose: real HTTP requests contend in
Starlette's sync thread pool and only the database compare-and-set stands
between them and a double claim.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.engines.write_lock import set_writer_lock, reset_writer_lock
from app.tests.deterministic import NullWriterLock


@pytest.fixture()
def client(db_dir):
    from app.main import app
    with TestClient(app) as ac:
        yield ac


@pytest.fixture(autouse=True)
def _lock():
    set_writer_lock(NullWriterLock())
    yield
    reset_writer_lock()


def test_http_race_exactly_one_200_rest_409(client):
    wid = client.post("/api/wishes", json={"title": "HTTP 并发"}).json()["id"]
    n = 16

    def raid(i):
        return client.post(f"/api/wishes/{wid}/claim", json={"claimer": f"u{i}"})

    with ThreadPoolExecutor(max_workers=n) as pool:
        responses = list(pool.map(raid, range(n)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(200) == 1
    assert statuses.count(409) == n - 1
    # Stable loser error code.
    for r in responses:
        if r.status_code == 409:
            assert r.json()["detail"] == "locked"

    winner = next(r for r in responses if r.status_code == 200).json()["claimer"]

    card = next(w for w in client.get("/api/wishes").json() if w["id"] == wid)
    detail = client.get(f"/api/wishes/{wid}").json()
    mine_win = client.get("/api/mine", params={"claimer": winner}).json()
    losers = [f"u{i}" for i in range(n) if f"u{i}" != winner]
    mine_lost = {
        name: client.get("/api/mine", params={"claimer": name}).json()
        for name in losers
    }

    # Three routes pinned to the same winner, same lock fields.
    for view in (card, detail, mine_win[0]):
        assert view["status"] == "claimed"
        assert view["claimer"] == winner
        assert view["claimed_at"] == card["claimed_at"] == detail["claimed_at"]
        assert view["expires_at"] == card["expires_at"] == detail["expires_at"]
    assert all(rows == [] for rows in mine_lost.values())

    # Global invariant: never two claimed rows/claimers for one wish.
    assert client.get("/api/wishes").json()  # smoke: wall still readable
