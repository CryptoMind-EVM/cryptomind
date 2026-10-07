"""Startup coordination for database-backed requests.

Two coordination channels coexist:

- **Async** (`_db_ready_event`): awaited by async callers via
  ``wait_for_db_ready()`` (e.g. the ``get_async_session`` FastAPI dep).
- **Sync** (`_db_ready_sync`): polled by sync callers via
  ``wait_for_db_ready_sync()``. This exists so ``get_connection()`` — which
  runs both on executor threads (no event loop) and, regrettably, inline on
  the event loop from some legacy routes — can wait for the lifespan
  background init instead of racing it by running ``init_db()`` itself.

Both channels are set/reset together by ``mark_db_ready`` /
``mark_db_failed`` / ``reset_db_ready_state`` so async and sync callers
observe a consistent state.

``_db_init_started`` is the "is a lifespan managing init?" signal: set by
``mark_db_init_started()`` at the top of lifespan's background init, it lets
``get_connection()`` distinguish the server context (wait for the managed
init) from non-server contexts (scripts/tests with no lifespan → fall back
to lazy ``init_db()``).
"""

from __future__ import annotations

import asyncio
import threading
import time

_db_ready_event = asyncio.Event()
_db_ready_error: Exception | None = None

# Sync mirror of _db_ready_event. Set/reset in lockstep with the async event
# so sync callers (get_connection on a worker thread) can wait without an
# event loop.
_db_ready_sync = threading.Event()

# "Is a lifespan managing DB init?" — True once mark_db_init_started() runs.
# Lets get_connection() decide: wait (server) vs. lazy-init (script/test).
_db_init_started: bool = False


def reset_db_ready_state() -> None:
    """Mark database startup as in progress (clear all ready signals)."""
    global _db_ready_event, _db_ready_error, _db_ready_sync, _db_init_started
    _db_ready_event = asyncio.Event()
    _db_ready_error = None
    _db_ready_sync = threading.Event()
    _db_init_started = False


def mark_db_init_started() -> None:
    """Signal that a lifespan-driven DB init is underway.

    Called at the top of lifespan's background init task. After this,
    ``get_connection()`` will wait for the managed init rather than running
    ``init_db()`` itself (which would race the background task).
    """
    global _db_init_started
    _db_init_started = True


def is_db_init_managed() -> bool:
    """Return True iff a lifespan has started managing DB init.

    False in scripts/tests that never run FastAPI lifespan; those callers
    must fall back to lazy ``init_db()``.
    """
    return _db_init_started


def mark_db_ready() -> None:
    """Signal that database initialization and migrations are complete."""
    global _db_ready_error
    _db_ready_error = None
    _db_ready_event.set()
    _db_ready_sync.set()


def mark_db_failed(exc: Exception) -> None:
    """Signal that database initialization failed."""
    global _db_ready_error
    _db_ready_error = exc
    _db_ready_event.set()
    _db_ready_sync.set()


def is_db_ready() -> bool:
    """Return True only when startup completed successfully."""
    return _db_ready_event.is_set() and _db_ready_error is None


def get_db_ready_error() -> Exception | None:
    """Return the exception that caused DB init to fail, or None.

    Used by health/readiness endpoints to distinguish three states without
    performing a DB round-trip:

    - ``is_db_init_managed() == False`` — not a lifespan-managed context
      (scripts/tests); caller should run its own DB check.
    - ``is_db_init_managed() and get_db_ready_error() is not None`` — init
      ran and failed; report unhealthy.
    - ``is_db_init_managed() and is_db_ready() == False`` — init is still
      in progress; report "warming up" instead of blocking on DB I/O.
    """
    return _db_ready_error


async def wait_for_db_ready(timeout: float = 45.0) -> None:
    """Wait for database startup to finish or raise on timeout/failure."""
    if is_db_ready():
        return

    try:
        await asyncio.wait_for(_db_ready_event.wait(), timeout=timeout)
    except TimeoutError as exc:
        raise RuntimeError("Database initialization is still in progress") from exc

    if _db_ready_error is not None:
        raise RuntimeError("Database initialization failed") from _db_ready_error


def wait_for_db_ready_sync(
    timeout: float = 60.0, poll_interval: float = 0.01
) -> None:
    """Synchronous wait for DB readiness, safe to call without an event loop.

    Polls the sync gate in short increments (``poll_interval``, default
    10 ms). The short granularity keeps a caller that *is* on an event loop
    from blocking it for the whole wait — while not ideal, this is far better
    than the previous behavior (running ``init_db()`` for ~49s inline).
    Callers fully off the loop (executor threads) are unaffected by the
    polling.

    Raises:
        RuntimeError: if init was marked failed (chained from the original
            error) or if the wait exceeds ``timeout``.
    """
    deadline = time.monotonic() + timeout
    while True:
        # Failure is signaled the moment it happens — bail out immediately
        # rather than making every subsequent request re-attempt init_db.
        if _db_ready_error is not None:
            raise RuntimeError(
                "Database initialization failed"
            ) from _db_ready_error
        if _db_ready_sync.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Database initialization is still in progress")
        # Never block longer than poll_interval, so an inline event-loop
        # caller yields periodically.
        _db_ready_sync.wait(timeout=min(poll_interval, remaining))
