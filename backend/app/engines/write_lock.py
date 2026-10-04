"""Per-wish writer locks.

A writer lock serializes claim/release/sweep mutations of a single wish,
collapsing read-decide-write into one critical section. Locks are keyed by
wish id, so distinct wishes never block each other.

The factory is dependency-injectable (``set_factory``) so tests can install a
deterministic lock that fixes which racer acquires first.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Protocol


class WriterLock(Protocol):
    @contextmanager
    def for_wish(self, wish_id: int) -> Iterator[None]: ...


class ThreadWriterLock:
    """Default: one reentrant lock per wish id (process-local mutex)."""

    def __init__(self) -> None:
        self._guards: dict[int, threading.RLock] = {}
        self._registry_lock = threading.Lock()

    @contextmanager
    def for_wish(self, wish_id: int) -> Iterator[None]:
        with self._registry_lock:
            guard = self._guards.get(wish_id)
            if guard is None:
                guard = self._guards[wish_id] = threading.RLock()
        with guard:
            yield


_current: WriterLock = ThreadWriterLock()


def get_writer_lock() -> WriterLock:
    """Return the active writer-lock implementation."""
    return _current


def set_writer_lock(lock: WriterLock) -> None:
    """Replace the active lock (tests inject a deterministic one)."""
    global _current
    _current = lock


def reset_writer_lock() -> None:
    global _current
    _current = ThreadWriterLock()
