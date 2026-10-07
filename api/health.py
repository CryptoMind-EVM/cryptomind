"""Health check endpoints for load balancing and monitoring."""

import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.utils import logger, run_sync

router = APIRouter(tags=["health"])

# Service start time for uptime calculation
SERVICE_START_TIME = time.time()


def _check_db(get_connection_fn, retries=2):
    """Run SELECT 1 with a single retry to absorb transient pool blips.

    /health and /ready share this; an intermittent failure (seen: /health
    passes while /ready fails seconds later) should not flip readiness when
    the immediate retry succeeds. Persistent failures still surface via the
    caller's except block (which logs the exception).

    Note: psycopg2 connection objects do NOT have execute() — must use a
    cursor. The previous `conn.execute("SELECT 1")` raised AttributeError
    every time, so /health and /ready ALWAYS reported database=False and
    the service was permanently marked unhealthy/degraded.
    """
    last_exc = None
    for _ in range(retries):
        conn = None
        cursor = None
        try:
            conn = get_connection_fn()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            last_exc = e
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
    if last_exc is not None:
        raise last_exc


@router.get("/health")
async def health_check():
    """Basic health check — verifies application is alive.

    Cold-start aware: during lifespan-managed DB initialization, this endpoint
    returns 200 immediately WITHOUT touching the database. Rationale:
    Zeabur's healthcheck runs every 30s with a 3s timeout; if /health calls
    ``get_connection()`` while init_db / Alembic is still running, the request
    will block on ``wait_for_db_ready_sync(120s)`` → healthcheck itself times
    out → container gets killed → restart → init restarts → infinite loop.

    States reported:
    - ``initializing`` (HTTP 200): DB init in progress; not ready for users
      but the process is alive, so don't kill it. Use ``/ready`` for the
      user-facing readiness gate (it does a real SELECT 1).
    - ``db_init_failed`` (HTTP 503): init ran and failed; do kill + restart.
    - ``healthy`` / ``degraded`` (HTTP 200 / 503): post-init, real SELECT 1.

    Non-server contexts (no lifespan, e.g. scripts/tests) fall through to
    the real DB check because there is no managed init to wait for.
    """
    checks = {"app": True}

    from core.db_ready import (
        get_db_ready_error,
        is_db_init_managed,
        is_db_ready,
    )

    if is_db_init_managed():
        # Server context: avoid any DB I/O during init.
        init_error = get_db_ready_error()
        if init_error is not None:
            checks["database"] = False
            checks["init_error"] = str(init_error)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "db_init_failed",
                    "service": "pi_crypto_insight",
                    "uptime_seconds": int(time.time() - SERVICE_START_TIME),
                    "checks": checks,
                },
            )
        if not is_db_ready():
            # Cold-start window: process is alive, DB is warming up.
            checks["database"] = "warming_up"
            return JSONResponse(
                status_code=200,
                content={
                    "status": "initializing",
                    "service": "pi_crypto_insight",
                    "uptime_seconds": int(time.time() - SERVICE_START_TIME),
                    "checks": checks,
                },
            )
        # Init done — fall through to real pool check below.

    try:
        from core.database import get_connection

        await run_sync(lambda: _check_db(get_connection))
        checks["database"] = True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("/health DB check failed: %s", e)
        checks["database"] = False

    all_ok = all(v is True for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "healthy" if all_ok else "degraded",
            "service": "pi_crypto_insight",
            "uptime_seconds": int(time.time() - SERVICE_START_TIME),
            "checks": checks,
        },
    )


@router.get("/ready")
async def readiness_check():
    """
    Readiness check — confirms the service can accept requests.
    Verifies critical components are initialized.
    """
    ready = True
    components = {}

    # 檢查數據庫
    try:
        from core.database import get_connection

        await run_sync(lambda: _check_db(get_connection))
        components["database"] = True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        # Log the actual exception so transient vs persistent failures are
        # diagnosable. Previously this was swallowed silently, making
        # /ready 503 impossible to root-cause from logs.
        logger.warning("/ready DB check failed: %s", e)
        components["database"] = False
        ready = False

    status_code = 200 if ready else 503

    return JSONResponse(
        content={
            "status": "ready" if ready else "not_ready",
            "components": components,
            "uptime_seconds": int(time.time() - SERVICE_START_TIME),
        },
        status_code=status_code,
    )
