"""Manifund MCP client — Discover API 的資料源（Phase 3，唯讀）。

與 agent bootstrap 的 mcp_loader 分離：這裡是 API 請求路徑用的輕量 client，
- 只讀（9 個唯讀工具）；
- server 級開關（MCP_ENABLED + MANIFUND_MCP_ENABLED），關閉或失敗一律
  fail-soft 拋 ``ManifundUnavailableError``，由 router 轉 503 降級訊息；
- 工具集快取（TTL），避免每個請求都連線 MCP server。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from api.utils import logger
from core.tools.mcp_loader import _DEFAULT_SERVERS, connection_spec

MANIFUND_SERVER_NAME = "manifund"
_TOOL_CACHE_TTL_SECONDS = 600

_tools_cache: Dict[str, Any] = {}
_tools_cache_at: float = 0.0
_tools_lock = asyncio.Lock()


class ManifundUnavailableError(RuntimeError):
    """Manifund 資料暫時無法取得（關閉／連線失敗／逾時）。"""


def _enabled() -> bool:
    from core.feature_flags import manifund_mcp_enabled
    from core.tools.mcp_loader import _is_enabled

    return _is_enabled() and manifund_mcp_enabled()


def _manifund_spec() -> dict:
    spec = _DEFAULT_SERVERS.get(MANIFUND_SERVER_NAME)
    if not spec:
        raise ManifundUnavailableError("manifund server spec missing")
    return spec


async def get_manifund_tools() -> Dict[str, Any]:
    """取得 Manifund 工具集（模組級快取 + TTL）。"""
    global _tools_cache, _tools_cache_at

    if not _enabled():
        raise ManifundUnavailableError("Manifund MCP is disabled")

    now = time.monotonic()
    if _tools_cache and now - _tools_cache_at < _TOOL_CACHE_TTL_SECONDS:
        return _tools_cache

    async with _tools_lock:
        if _tools_cache and time.monotonic() - _tools_cache_at < _TOOL_CACHE_TTL_SECONDS:
            return _tools_cache
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            client = MultiServerMCPClient(
                connections={MANIFUND_SERVER_NAME: connection_spec(_manifund_spec())}
            )
            tools = await asyncio.wait_for(client.get_tools(), timeout=20.0)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.warning("[discover] manifund MCP fetch failed: %s", exc)
            raise ManifundUnavailableError("Manifund data temporarily unavailable") from exc

        _tools_cache = {getattr(t, "name", str(t)): t for t in tools}
        _tools_cache_at = time.monotonic()
        return _tools_cache


def _unwrap_content(result: Any) -> Any:
    """解包 MCP 工具回應的 content blocks。

    langchain-mcp-adapters 的工具回 ``[{type: "text", text: "<json>"}]``
    （Manifund 實測 2026-08-15）；text 本身多为 JSON 字串——解開回真實
    payload；解不開（純文字）就回原字串。非 text-block 結構原樣回傳。
    """
    if (
        isinstance(result, list)
        and result
        and isinstance(result[0], dict)
        and result[0].get("type") == "text"
    ):
        text = result[0].get("text", "")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return text
        return text
    return result


async def call_manifund_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    """呼叫單一 Manifund 工具（20s timeout，fail-soft）；回應已解包為真實 payload。"""
    tools = await get_manifund_tools()
    tool = tools.get(tool_name)
    if tool is None:
        raise ManifundUnavailableError(f"tool '{tool_name}' not available")
    try:
        raw = await asyncio.wait_for(tool.ainvoke(args), timeout=20.0)
        return _unwrap_content(raw)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except ManifundUnavailableError:
        raise
    except Exception as exc:
        logger.warning("[discover] manifund tool '%s' failed: %s", tool_name, exc)
        raise ManifundUnavailableError("Manifund data temporarily unavailable") from exc
