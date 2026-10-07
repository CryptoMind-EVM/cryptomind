"""
Langfuse 初始化與 LangChain CallbackHandler 工廠

基於 Langfuse Python SDK v4（OTel-based，2025-05 起的官方 SDK）。

設計目標
--------
- **零侵入**：agent / LLM client 完全不知道 Langfuse 存在；只在 graph 執行邊界
  注入一個 CallbackHandler，LangChain 1.x 的 callback 機制會自動把它往下傳到
  所有內層 LLM 呼叫、tool 呼叫、ReAct loop。
- **fail-safe**：未設 keys、TEST_MODE、或 SDK 初始化失敗時，自動 no-op，
  ``get_langchain_handler`` / ``trace_graph_call`` 直接 return / 原樣執行。
- **OTel context 正確**：v4 的 user_id / session_id 透過 OTel baggage 上達 trace。
  baggage 是 **context-bound**，只在 ``with`` 區塊內有效。因此我們不能「建立
  handler 後離開 with」—— 必須在 with 區塊內**呼叫 graph**，讓 callback 觸發
  時 context 還在。

環境變數（v4 官方名稱）
-----------------------
- ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``：Project Settings 取得
- ``LANGFUSE_HOST`` 或 ``LANGFUSE_BASE_URL``：Cloud region URL（含 schema）
- ``LANGFUSE_TRACING_ENABLED``：明確開關；未設時由本模組依 keys / TEST_MODE 推導

使用方式
--------
.. code-block:: python

    # 啟動時呼叫一次（api/lifespan.py）
    from utils.langfuse_init import init_langfuse
    init_langfuse()

    # graph 執行：用 trace_graph_call 包起來（同步 + 非同步版本）
    from utils.langfuse_init import trace_graph_call, atrace_graph_call
    handler = get_langchain_handler()
    config = {"callbacks": [handler]} if handler else None
    async with atrace_graph_call(user_id, session_id, metadata):
        result = await graph.ainvoke(input, config)
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, AsyncIterator, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

# 模組級狀態：是否已成功初始化（lifespan 期間固定，request 共享）。
_langfuse_enabled: bool = False
_init_attempted: bool = False


def _resolve_env_bool(name: str, default: bool) -> bool:
    """解析 boolean 環境變數（接受 true/false/1/0/yes/no，不分大小寫）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _should_force_disable() -> Optional[str]:
    """回傳強制停用的原因字串；若不應停用則回 None。"""
    if _resolve_env_bool("TEST_MODE", default=False):
        return "TEST_MODE=true"
    # 允許環境顯式關閉（例如緊急下線observability）
    explicit = os.getenv("LANGFUSE_TRACING_ENABLED")
    if explicit is not None and explicit.strip().lower() in {"0", "false", "no", "off"}:
        return "LANGFUSE_TRACING_ENABLED=false"
    return None


def init_langfuse() -> bool:
    """初始化 Langfuse 客戶端。在 application lifespan startup 呼叫一次。

    依下列優先序決定是否啟用：
    1. ``TEST_MODE=true`` → 永遠停用（避免測試流量進入 production dashboard）
    2. ``LANGFUSE_TRACING_ENABLED=false`` → 顯式停用
    3. 缺 ``LANGFUSE_PUBLIC_KEY`` 或 ``LANGFUSE_SECRET_KEY`` → 停用
    4. 否則：呼叫 ``Langfuse.auth_check()`` 驗證 keys 有效；失敗則停用並 log

    Returns:
        True 若 Langfuse 啟用且可上報；False 表示 disabled（no-op 模式）。

    Notes:
        - 本函式可重複呼叫；只會在第一次真正嘗試，之後回傳快取結果。
        - 任何例外都被捕捉，僅 log warning；不影響 app 啟動。
    """
    global _langfuse_enabled, _init_attempted
    if _init_attempted:
        return _langfuse_enabled
    _init_attempted = True

    reason = _should_force_disable()
    if reason:
        logger.info("📊 Langfuse disabled (%s)", reason)
        _langfuse_enabled = False
        return False

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        logger.info(
            "📊 Langfuse disabled (no LANGFUSE_PUBLIC_KEY/SECRET_KEY — "
            "set them in .env to enable LLM observability)"
        )
        _langfuse_enabled = False
        return False

    host = (
        os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    ).strip()
    try:
        from langfuse import get_client

        client = get_client()
        # auth_check 是阻塞呼叫，預設沒 timeout。包在 thread + join(timeout) 內，
        # 避免 Langfuse server 慢或 DNS 卡時擋住整個 lifespan 啟動。
        # 5 秒足夠正常 auth；超過就降級為 disabled（trace 不會上報但 app 不卡）。
        import threading

        auth_result: dict = {"ok": None, "exc": None}

        def _do_auth_check() -> None:
            try:
                client.auth_check()
                auth_result["ok"] = True
            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception as exc:  # noqa: BLE001
                auth_result["exc"] = exc

        t = threading.Thread(target=_do_auth_check, daemon=True)
        t.start()
        t.join(timeout=5.0)

        if t.is_alive():
            # thread 還在跑 = timeout。daemon=True 所以 process 結束會被收掉。
            logger.warning(
                "⚠️ Langfuse auth_check timed out (>5s, host=%s) — "
                "tracing disabled to avoid blocking startup",
                host,
            )
            _langfuse_enabled = False
            return False

        if auth_result["exc"] is not None:
            logger.warning(
                "⚠️ Langfuse auth_check failed (host=%s): %s — tracing disabled",
                host,
                auth_result["exc"],
            )
            _langfuse_enabled = False
            return False

        if auth_result["ok"] is True:
            _langfuse_enabled = True
            logger.info("📊 Langfuse enabled (host=%s)", host)
            return True

        # 不該到這裡（ok/exc 都 None），保守降級
        logger.warning("⚠️ Langfuse auth_check returned unknown state — tracing disabled")
        _langfuse_enabled = False
        return False
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — 初始化失敗絕不擋 app 啟動
        logger.warning("⚠️ Langfuse initialization failed: %s — tracing disabled", exc)
        _langfuse_enabled = False
        return False


def is_langfuse_enabled() -> bool:
    """目前是否啟用 Langfuse tracing。"""
    return _langfuse_enabled


def get_langchain_handler(
    public_key: Optional[str] = None,
) -> Optional[Any]:
    """取得 LangChain ``CallbackHandler``。

    在 v4，user_id / session_id / metadata 不在 handler 上設定，而是透過 OTel
    baggage 上達 trace。本函式**只**回傳 handler；呼叫端必須在
    ``trace_graph_call`` / ``atrace_graph_call`` 的 ``with`` 區塊內使用這個
    handler（即執行 graph），baggage 才會在 callback 觸發時有效。

    Args:
        public_key: 多 project 時指定；預設用環境變數的 default client。

    Returns:
        ``langfuse.langchain.CallbackHandler`` 實例；disabled 時回 ``None``。
    """
    if not _langfuse_enabled:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(public_key=public_key)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — handler 建立失敗不擋 graph 執行
        logger.warning("⚠️ Langfuse handler creation failed: %s — skipping", exc)
        return None


@contextlib.contextmanager
def trace_graph_call(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_name: Optional[str] = None,
) -> Iterator[None]:
    """建立 Langfuse root span + 寫入 OTel baggage，讓內層 CallbackHandler
    產生的 observations 都附上同一個 trace 與 user/session/metadata。

    必須在 ``with`` 區塊內執行 graph.ainvoke / invoke，且 handler 已放進
    ``config["callbacks"]``。離開 with 時 root span 自動 end + 上報。

    Yields:
        無；pure context manager。

    Notes:
        - Langfuse disabled 時為真空操作（直接 yield）。
        - 內部例外只 log warning，不影響 graph 執行。
    """
    if not _langfuse_enabled:
        yield
        return
    try:
        from langfuse import get_client, propagate_attributes

        client = get_client()
        name = trace_name or f"chat:{session_id or 'default'}"
        with client.start_as_current_observation(name=name, as_type="chain"), \
             propagate_attributes(
                 user_id=user_id or "anonymous",
                 session_id=session_id,
                 metadata=metadata or {},
             ):
            yield
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — context 失敗不擋 graph
        logger.warning("⚠️ Langfuse trace context failed: %s — tracing skipped", exc)
        yield


@contextlib.asynccontextmanager
async def atrace_graph_call(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_name: Optional[str] = None,
) -> AsyncIterator[None]:
    """``trace_graph_call`` 的非同步版本。語意完全相同，給 ``await graph.ainvoke``
    用的。v4 的 OTel context manager 是 agnostic（同步 with 也能在 async 內用），
    但提供 async 版讓呼叫端意圖清晰、未來若 SDK 改 API 也只動這層。
    """
    if not _langfuse_enabled:
        yield
        return
    try:
        from langfuse import get_client, propagate_attributes

        client = get_client()
        name = trace_name or f"chat:{session_id or 'default'}"
        with client.start_as_current_observation(name=name, as_type="chain"), \
             propagate_attributes(
                 user_id=user_id or "anonymous",
                 session_id=session_id,
                 metadata=metadata or {},
             ):
            yield
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — context 失敗不擋 graph
        logger.warning("⚠️ Langfuse trace context failed: %s — tracing skipped", exc)
        yield


def shutdown_langfuse() -> None:
    """Application shutdown 時呼叫，排空尚未上報的 trace。

    Langfuse SDK 採 batched async upload；不 flush 會丟失最後一批 span。
    """
    if not _langfuse_enabled:
        return
    try:
        from langfuse import get_client

        get_client().shutdown()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — shutdown 失敗不擋 app 關閉
        logger.warning("⚠️ Langfuse shutdown failed: %s", exc)


__all__ = [
    "init_langfuse",
    "is_langfuse_enabled",
    "get_langchain_handler",
    "trace_graph_call",
    "atrace_graph_call",
    "shutdown_langfuse",
]
