"""
SQLAlchemy 2.0 Async engine and session factory.

Provides a shared async engine and sessionmaker that can be used
throughout the application. The engine is lazily initialized on first use.

Usage::

    from core.orm.session import get_async_session

    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.user_id == "uid"))
        user = result.scalar_one_or_none()
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import weakref
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from core.config import TEST_MODE
from core.db_ready import wait_for_db_ready

logger = logging.getLogger(__name__)

_async_engine = None
_async_engine_loop_ref = None  # weakref 到 engine 建立時的 running loop（loop 變更偵測）
_async_session_factory = None
_engine_lock = threading.Lock()
_factory_lock = threading.Lock()


def _normalize_pg_url(url: str) -> str:
    """Convert any postgres:// or postgresql:// URL to asyncpg format."""
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    url = url.replace("sslmode=require", "ssl=require", 1)
    url = url.replace("&channel_binding=require", "", 1)
    return url


def _resolve_async_url() -> str | None:
    """Build an async PostgreSQL URL from environment variables.

    Checks multiple naming conventions used by different hosting providers
    (Zeabur uses POSTGRES_* prefix; others use POSTGRESQL_* or DATABASE_URL).
    """
    # Try connection string / URI first (Zeabur auto-generated vars)
    for uri_var in ("DATABASE_URL", "POSTGRES_URI", "POSTGRES_CONNECTION_STRING"):
        raw = os.getenv(uri_var)
        if raw:
            return _normalize_pg_url(raw)

    # Try individual components — support both POSTGRESQL_* and POSTGRES_* prefixes
    host = os.getenv("POSTGRESQL_HOST") or os.getenv("POSTGRES_HOST")
    user = (
        os.getenv("POSTGRESQL_USER")
        or os.getenv("POSTGRES_USERNAME")
        or os.getenv("POSTGRES_USER")
    )
    password = os.getenv("POSTGRESQL_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
    db_name = (
        os.getenv("POSTGRESQL_DB")
        or os.getenv("POSTGRES_DB")
        or os.getenv("POSTGRES_DATABASE")
    )
    port = os.getenv("POSTGRESQL_PORT") or os.getenv("POSTGRES_PORT") or "5432"

    if not all([host, user, password, db_name]):
        return None

    encoded_user = quote(str(user), safe="")
    encoded_password = quote(str(password), safe="")
    encoded_db = quote(str(db_name), safe="")

    return (
        f"postgresql+asyncpg://{encoded_user}:{encoded_password}"
        f"@{host}:{port}/{encoded_db}"
    )


def get_engine():
    """Return the global async engine, creating it if needed.

    Loop 變更偵測（2026-08-25）：analysis_worker 每個 job 用 ``asyncio.run``
    跑在新 event loop 上，但 engine/pool 是 module singleton——綁死第一個
    loop 後，後續 job 的 await 會炸 "Future attached to a different loop"
    （線上：HITL resume 的 guard audit receipt 兩次被吃掉）。偵測到 running
    loop 變更時 dispose 舊 engine 重建，session factory 由 get_session_factory
    依 engine 身分自動跟進。

    loop 身分用 weakref 比較（不能用 ``id()``：舊 loop 回收後新 loop 可能
    落在同一個記憶體位址，id 會誤判「沒變」）。
    """
    global _async_engine, _async_engine_loop_ref
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _async_engine is not None and current_loop is not None:
        prev_loop = _async_engine_loop_ref() if _async_engine_loop_ref else None
        if prev_loop is not current_loop:
            with _engine_lock:
                prev_loop = _async_engine_loop_ref() if _async_engine_loop_ref else None
                if prev_loop is not current_loop:
                    logger.info(
                        "[orm.session] event loop changed — recreating async engine"
                    )
                    try:
                        _async_engine.sync_engine.dispose()
                    except Exception:
                        pass
                    _async_engine = None

    if _async_engine is None:
        with _engine_lock:
            if _async_engine is None:
                url = _resolve_async_url()
                if not url:
                    raise RuntimeError(
                        "Cannot create async engine: no DATABASE_URL or "
                        "POSTGRESQL_* variables set"
                    )
                pool_size = int(os.getenv("DB_MAX_POOL_SIZE", "10"))
                # TEST_MODE（pytest-asyncio 每個測試用新 event loop）下，asyncpg
                # 的 connection pool 會綁死第一個 loop，跨測試重用就報
                # "got Future attached to a different loop"。測試用 NullPool
                # （每個 session 新連線）根治；production（單一 loop）維持 pooling。
                if TEST_MODE:
                    _async_engine = create_async_engine(
                        url,
                        poolclass=NullPool,
                        pool_pre_ping=True,
                        connect_args={
                            "server_settings": {
                                "statement_timeout": os.getenv(
                                    "DB_STATEMENT_TIMEOUT", "10000"
                                ),
                            },
                        },
                    )
                    logger.info(
                        "Async SQLAlchemy engine created (TEST_MODE → NullPool)"
                    )
                else:
                    _async_engine = create_async_engine(
                        url,
                        pool_size=pool_size,
                        max_overflow=pool_size,
                        pool_pre_ping=True,
                        pool_recycle=300,
                        connect_args={
                            "server_settings": {
                                "statement_timeout": os.getenv(
                                    "DB_STATEMENT_TIMEOUT", "10000"
                                ),
                            },
                        },
                    )
                    logger.info("Async SQLAlchemy engine created (pool_size=%d)", pool_size)
                _async_engine_loop_ref = (
                    weakref.ref(current_loop) if current_loop is not None else None
                )
    return _async_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the global async session factory（engine 重建時自動跟進）。"""
    global _async_session_factory
    engine = get_engine()
    if (
        _async_session_factory is None
        or _async_session_factory.kw.get("bind") is not engine
    ):
        with _factory_lock:
            engine = get_engine()
            if (
                _async_session_factory is None
                or _async_session_factory.kw.get("bind") is not engine
            ):
                _async_session_factory = async_sessionmaker(
                    engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                )
    return _async_session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    try:
        await wait_for_db_ready()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is still initializing. Please retry shortly.",
        ) from exc

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            await session.rollback()
            raise


async def close_async_engine():
    """Dispose the async engine (call on shutdown)."""
    global _async_engine, _async_session_factory
    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None
        logger.info("Async SQLAlchemy engine disposed")


def close_async_engine_sync() -> None:
    """Dispose the async engine from sync contexts like Gunicorn hooks."""
    if _async_engine is None:
        return

    asyncio.run(close_async_engine())


@asynccontextmanager
async def using_session(
    session: AsyncSession | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a session for repo use.

    If *session* is provided (e.g. from a FastAPI dependency), use it
    directly without committing or closing — the caller manages the
    lifecycle.  Otherwise create, commit, and close a new session.
    """
    if session is not None:
        yield session
    else:
        factory = get_session_factory()
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                await s.rollback()
                raise
