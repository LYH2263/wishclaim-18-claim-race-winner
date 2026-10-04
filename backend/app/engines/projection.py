"""Read projections shared by the wall, detail and "my claims" routes.

All three views are pinned by the same projection: a claimed wish carries the
winner's ``claimer``/``claimed_at``/``expires_at`` everywhere; a loser never
appears. Stale locks are swept through the race gate before projecting so the
three routes cannot disagree about who owns a wish.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.engines.race_gate import sweep_expired

# Single source of truth for the fields every route may expose.
PINNED_FIELDS = ("id", "title", "note", "status", "claimer",
                 "claimed_at", "expires_at", "data_quality")


def project(row: sqlite3.Row | dict) -> dict:
    """Project one row onto the pinned view used by all three read routes."""
    d = dict(row)
    return {k: d.get(k) for k in PINNED_FIELDS}


def refresh(c: sqlite3.Connection, now: datetime) -> None:
    """Apply TTL releases before reads so projections cannot show a ghost."""
    sweep_expired(c, now)


def wall(c: sqlite3.Connection, now: datetime) -> list[dict]:
    refresh(c, now)
    rows = c.execute("SELECT * FROM wishes ORDER BY id DESC").fetchall()
    return [project(r) for r in rows]


def detail(c: sqlite3.Connection, wish_id: int, now: datetime) -> dict | None:
    refresh(c, now)
    row = c.execute("SELECT * FROM wishes WHERE id=?", (wish_id,)).fetchone()
    return project(row) if row else None


def mine(c: sqlite3.Connection, claimer: str, now: datetime) -> list[dict]:
    refresh(c, now)
    rows = c.execute(
        "SELECT * FROM wishes WHERE claimer=? ORDER BY id DESC", (claimer,)
    ).fetchall()
    return [project(r) for r in rows]
