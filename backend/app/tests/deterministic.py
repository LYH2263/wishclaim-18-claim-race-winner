"""Deterministic concurrency primitives for race tests.

``DeterministicWriterLock`` removes OS scheduling from the outcome: every
contender blocks inside the critical section until the test starts the race,
after which contenders are admitted strictly serially in a scripted identity
order. Winner and loser are therefore fixed and reproducible — no sleeps,
no retries, no "usually wins".
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class NullWriterLock:
    """No serialization at all — only the database CAS can prevent a race."""

    @contextmanager
    def for_wish(self, wish_id: int):
        yield


class DeterministicWriterLock:
    def __init__(self) -> None:
        self._local = threading.local()
        self._m = threading.Lock()
        self._state: dict[int, dict] = {}

    def declare(self, name: str) -> None:
        """Bind the calling thread to a contender identity."""
        self._local.name = name

    def script(self, wish_id: int, order: list[str]) -> None:
        """Fix the admission order for contenders of one wish."""
        self._state[wish_id] = {
            "order": list(order),
            "arrived": set(),
            "admit": {name: threading.Event() for name in order},
            "done": {name: threading.Event() for name in order},
        }

    @contextmanager
    def for_wish(self, wish_id: int):
        name = getattr(self._local, "name", None)
        st = self._state.get(wish_id)
        # Bystander threads (e.g. a read-path sweep) are never scripted:
        # let them straight through.
        if st is None or name is None or name not in st["order"]:
            yield
            return
        with self._m:
            st["arrived"].add(name)
        if not st["admit"][name].wait(timeout=5):
            raise RuntimeError(f"contender {name!r} was never admitted")
        try:
            yield
        finally:
            st["done"][name].set()

    def release_race(self, wish_id: int, timeout: float = 5.0) -> None:
        """Wait for every contender to queue, then admit them in order.

        Contender N+1 is admitted only after contender N has fully left the
        critical section, so the winner commits before the loser reads.
        """
        st = self._state[wish_id]
        deadline = time.monotonic() + timeout
        while len(st["arrived"]) < len(st["order"]) and time.monotonic() < deadline:
            time.sleep(0.001)
        assert st["arrived"] == set(st["order"]), (
            f"missing contenders: {set(st['order']) - st['arrived']}")
        order = st["order"]
        for i, name in enumerate(order):
            st["admit"][name].set()
            if i < len(order) - 1:
                assert st["done"][name].wait(timeout=5), f"{name!r} never finished"
