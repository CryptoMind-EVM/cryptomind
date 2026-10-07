"""
Tool result compaction helpers.

Wrap large LangChain tool outputs to avoid flooding LangGraph message state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

import orjson
from langchain_core.tools import BaseTool

from core.memory_scope import build_scope, scope_namespace

logger = logging.getLogger(__name__)

THRESHOLD = 2_000
# 預覽 budget：舊值 500 對 list 型工具輸出（如新聞）太小——單則含長 URL 的
# 新聞 JSON 就佔滿 500 字元，LLM 只看得到 1 則。提高後多則新聞的 title/
# date/source 都能容納，LLM 能引用完整清單。
PREVIEW_LEN = 2_000
REDIS_TTL = 3_600
_KEY_PREFIX = "tr:"
MAX_LOCAL_STORE_SIZE = 1000

_local_store: dict[str, str] = {}
_redis_client: Optional[Any] = None
_redis_init_attempted = False
_wrapped_tool_ids: set[int] = set()
_tool_stats: dict[int, Optional[dict]] = {}


def _serialize_record(
    data: str,
    owner_id: Optional[str],
    workspace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    owner_scope = None
    if owner_id is not None:
        owner_scope = scope_namespace(
            build_scope(owner_id, session_id=session_id, workspace_id=workspace_id)
        )
    return orjson.dumps(
        {
            "data": data,
            "owner_id": owner_id,
            "owner_scope": owner_scope,
        }
    ).decode()


def _deserialize_record(raw: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if raw is None:
        return None, None, None

    if isinstance(raw, bytes):
        raw = raw.decode()

    if not isinstance(raw, str):
        return None, None, str(raw)

    try:
        decoded = orjson.loads(raw)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None, None, raw

    if isinstance(decoded, dict) and "data" in decoded:
        owner_id = decoded.get("owner_id")
        if owner_id is not None:
            owner_id = str(owner_id)
        owner_scope = decoded.get("owner_scope")
        if owner_scope is not None:
            owner_scope = str(owner_scope)
        return owner_id, owner_scope, str(decoded["data"])

    return None, None, raw


def _get_redis_sync() -> Optional[Any]:
    """Return a synchronous Redis client or None when unavailable."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client

    _redis_init_attempted = True
    try:
        import redis as _r

        from core.redis_url import resolve_redis_url

        url, _ = resolve_redis_url()
        if not url:
            return None

        client = _r.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
        logger.info("[ToolCompactor] Redis sync client connected")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[ToolCompactor] Redis unavailable, using local fallback: %s", exc
        )
        _redis_client = None
    return _redis_client


def _to_str(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return orjson.dumps(output).decode()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return json.dumps(output, ensure_ascii=False, default=str)


def _smart_preview(text: str) -> str:
    """產生截斷預覽，對 list 型結構化輸出做「逐項保留」而非整串字元硬切。

    背景：google_news 等工具回傳 list of dict，序列化後常超過 THRESHOLD(2000)。
    舊版直接 ``text[:PREVIEW_LEN=500]`` 會在第二個項目的 JSON 中間切斷，產生
    無效 JSON — LLM 無法 parse，只能引用第 1 則，並回報「內容被截斷」。

    本函式偵測頂層 list：盡量保留最多「完整」項目（每項 JSON 合法可 parse），
    只在不超過 PREVIEW_LEN 的前提下取前 N 項。非 list 輸出退回原字元截斷。

    Args:
        text: 工具輸出序列化後的字串（``_to_str(raw)`` 的結果）。

    Returns:
        截斷後的預覽字串，含截斷提示。長度 ≤ PREVIEW_LEN + 提示文字。
    """
    # 嘗試 parse 為 JSON；只有頂層 list 才走結構感知路徑。
    try:
        parsed = orjson.loads(text)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        parsed = None

    if not isinstance(parsed, list) or len(parsed) == 0:
        # 非字串、單一 dict、parse 失敗 → 退回字元截斷（不影響既有路徑）
        preview = text[:PREVIEW_LEN]
        return (
            f"{preview}...\n"
            f"[完整資料已截斷，共 {len(text):,} 字。以上為前 {PREVIEW_LEN} 字預覽。]"
        )

    # 逐項序列化，累積到接近 PREVIEW_LEN 為止。
    # 每項用 orjson 序列化（緊湊），確保每項都是合法 JSON。
    budget = PREVIEW_LEN
    kept: list[str] = []
    for item in parsed:
        item_str = orjson.dumps(item).decode()
        # 第一項必須保留（即使超過 budget，否則空預覽毫無意義）
        if not kept:
            kept.append(item_str)
            budget -= len(item_str) + 1  # +1 for comma separator
            continue
        if len(item_str) + 1 > budget:
            break
        kept.append(item_str)
        budget -= len(item_str) + 1

    shown = len(kept)
    total = len(parsed)
    items_json = "[" + ", ".join(kept) + "]"
    if shown < total:
        note = (
            f"\n[以上為 {shown}/{total} 項。完整資料已截斷（共 {len(text):,} 字）。]"
        )
    else:
        note = f"\n[完整資料已截斷，共 {len(text):,} 字。]"
    return f"{items_json}{note}"


def _store_sync(
    data: str,
    owner_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    uid: Optional[str] = None,
) -> str:
    uid = uid or str(uuid.uuid4())
    record = _serialize_record(
        data, owner_id, workspace_id=workspace_id, session_id=session_id
    )
    redis_client = _get_redis_sync()
    if redis_client is not None:
        try:
            redis_client.setex(_KEY_PREFIX + uid, REDIS_TTL, record)
            return uid
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.debug("[ToolCompactor] Redis store failed: %s", exc)
    if len(_local_store) >= MAX_LOCAL_STORE_SIZE:
        keys_to_remove = list(_local_store.keys())[: MAX_LOCAL_STORE_SIZE // 5]
        for k in keys_to_remove:
            del _local_store[k]
    _local_store[uid] = record
    return uid


def _retrieve_sync(uid: str) -> Optional[tuple[Optional[str], Optional[str], str]]:
    redis_client = _get_redis_sync()
    if redis_client is not None:
        try:
            value = redis_client.get(_KEY_PREFIX + uid)
            if value is not None:
                owner_id, owner_scope, data = _deserialize_record(value)
                if data is not None:
                    return owner_id, owner_scope, data
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.debug("[ToolCompactor] Redis retrieve failed: %s", exc)
    value = _local_store.get(uid)
    if value is None:
        return None
    owner_id, owner_scope, data = _deserialize_record(value)
    if data is None:
        return None
    return owner_id, owner_scope, data


def _is_compactor_wrapped(tool: Any) -> bool:
    if id(tool) in _wrapped_tool_ids:
        return True
    # Use getattr with explicit bool check to avoid MagicMock returning truthy auto-created attrs
    val = getattr(tool, "_compactor_wrapped", None)
    return val is True


def _set_last_stat(tool: Any, stat: Optional[dict]) -> None:
    _tool_stats[id(tool)] = stat


def _get_last_stat(tool: Any) -> Optional[dict]:
    return _tool_stats.get(id(tool))


if not isinstance(getattr(BaseTool, "last_stat", None), property):
    BaseTool.last_stat = property(_get_last_stat, _set_last_stat)


# ── 輸出預算與分頁（2026-10-06，DANNY：「不該截斷這樣會有問題吧」）────────────────────────
# 舊做法：每個工具輸出超過 2000 字就只給前 2000 字，後面直接丟掉（而且刻意不給取回的 key）——
# us_stock_snapshot 5.3K 字只看得到 38%、新聞清單少三分之一。2000 字的由來是 context 有限（主機 slot 32K token，
# 完整 prompt 就吃掉 22K）。這裡改成「依本輪實際還剩多少 context 決定額度，超過不丟、改分頁讀」：
#   額度 ＝ slot context − 這輪 prompt（system＋展示的工具定義）− 歷史 − 答案預留，再換成字數；
#   每個工具呼叫最多拿 PER_CALL，整輪共用一份預算（TurnOutputBudget），用掉的從預算扣；
#   超過額度的輸出回「前一頁」＋明確續讀指引，模型要後面的內容就呼叫 read_more_result 分頁讀。
# 只有 execute_streaming（claw 主路徑）會掛預算；沒掛的路徑（測試、其他呼叫端）行為與舊版完全相同。
CHARS_PER_TOKEN = 2.2  # 保守估計（實測 prompt 約 2.4 字／token；JSON 約 3；中文約 1.5–2）
DEFAULT_SLOT_TOKENS = 32_768  # 主機 llama-server：-c 65536 -np 2；執行時會再問 /props 校正
ANSWER_RESERVE_TOKENS = 3_500  # 留給最後答案（長答案實測 1–3K token）
HISTORY_MIN_TOKENS = 1_500
FLOOR_CHARS = 2_000  # 預算用完後每次呼叫仍至少給這麼多（等同舊上限）
PER_CALL_LOCAL_CHARS = 8_000
PER_CALL_CLOUD_CHARS = 16_000
TURN_CLOUD_CHARS = 60_000
TURN_LOCAL_MAX_CHARS = 60_000


def output_budget_enabled() -> bool:
    """TOOL_OUTPUT_BUDGET（預設開；false/0/no/off ＝ 回到每個工具輸出一律 2000 字預覽）。"""
    return os.getenv("TOOL_OUTPUT_BUDGET", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class TurnOutputBudget:
    """一輪對話共用的工具輸出預算（平行工具呼叫在多執行緒，所以要鎖）。"""

    def __init__(self, total_chars: int, per_call_chars: int) -> None:
        self.total = max(int(total_chars), FLOOR_CHARS)
        self.per_call = max(int(per_call_chars), FLOOR_CHARS)
        self.used = 0
        self._lock = threading.Lock()

    def allowance(self) -> int:
        with self._lock:
            return max(FLOOR_CHARS, min(self.per_call, self.total - self.used))

    def consume(self, chars: int) -> None:
        with self._lock:
            self.used += max(0, int(chars))


def make_turn_budget(
    prompt_chars: int,
    history_chars: int,
    *,
    local: bool,
    slot_tokens: Optional[int] = None,
) -> TurnOutputBudget:
    """本輪預算。local＝平台本機模型（slot 只有 32K token，要精算）；其餘模型 context 大，給固定寬額度。"""
    if not local:
        return TurnOutputBudget(TURN_CLOUD_CHARS, PER_CALL_CLOUD_CHARS)
    slot = int(slot_tokens or DEFAULT_SLOT_TOKENS)
    prompt_tokens = prompt_chars / CHARS_PER_TOKEN
    history_tokens = max(HISTORY_MIN_TOKENS, history_chars / CHARS_PER_TOKEN)
    free_tokens = slot - prompt_tokens - history_tokens - ANSWER_RESERVE_TOKENS
    total = int(min(max(free_tokens, 0) * CHARS_PER_TOKEN, TURN_LOCAL_MAX_CHARS))
    return TurnOutputBudget(total, min(PER_CALL_LOCAL_CHARS, max(total, FLOOR_CHARS)))


_slot_tokens_cache: dict[str, int] = {}


async def slot_context_tokens(base_url: str) -> int:
    """問 llama-server /props 的每個 slot 的 context 大小（行程內快取）；失敗回保守預設值。"""
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    if not root:
        return DEFAULT_SLOT_TOKENS
    if root in _slot_tokens_cache:
        return _slot_tokens_cache[root]
    value = DEFAULT_SLOT_TOKENS
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(root + "/props")
        n_ctx = int(
            ((resp.json().get("default_generation_settings") or {}).get("n_ctx")) or 0
        )
        if 4_096 <= n_ctx <= 1_000_000:
            value = n_ctx
            _slot_tokens_cache[root] = value  # 只快取問到的；失敗下次還會再試
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 — 問不到就用保守預設值
        pass
    return value


def _json_array_item_ends(text: str) -> list[int]:
    """頂層 JSON 陣列每個元素結束後的位置（供分頁在「完整項目」處切）；不是陣列回空。"""
    ends: list[int] = []
    n = len(text)
    i = 0
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "[":
        return ends
    depth = 0
    in_str = False
    esc = False
    for j in range(i, n):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 1:
                ends.append(j + 1)
    return ends


def page_text(text: str, offset: int, limit: int) -> tuple[str, int]:
    """從 offset 起取一頁（最多 limit 字），回 (這頁文字, 切點)。切點 == len(text) 代表讀完。

    JSON 陣列在「完整項目」處切（每頁的項目都完整、可 parse）；其他文字盡量在換行／空白處切。
    """
    total = len(text)
    offset = max(0, min(int(offset), total))
    hard_end = min(total, offset + max(int(limit), 1))
    if hard_end >= total:
        cut = total
    else:
        cut = hard_end
        ends = [e for e in _json_array_item_ends(text) if offset < e <= hard_end]
        if ends:
            cut = ends[-1]
        else:
            window_start = offset + int((hard_end - offset) * 0.8)
            nl_at = text.rfind("\n", window_start, hard_end)
            if nl_at > offset:
                cut = nl_at + 1
            else:
                sp_at = text.rfind(" ", window_start, hard_end)
                if sp_at > offset:
                    cut = sp_at + 1
    page = text[offset:cut]
    if offset > 0 and text.lstrip().startswith("[") and page.startswith(","):
        page = page[1:].lstrip()  # 陣列從第二頁起以項目間逗號開頭
    return page, cut


def page_footer(ref: str, start: int, cut: int, total: int) -> str:
    """分頁提示（給模型看）。ref 是短代碼，不是 UUID。"""
    if cut >= total:
        return f"\n[第 {start + 1:,}–{total:,} 字，共 {total:,} 字，已讀完。]"
    return (
        f"\n[資料共 {total:,} 字，以上為第 {start + 1:,}–{cut:,} 字。"
        f"需要後面的內容請呼叫 read_more_result(ref=\"{ref}\", offset={cut})；"
        "已足夠回答就不必續讀。]"
    )


def attach_budget(tools: Any, budget: Optional[TurnOutputBudget]) -> int:
    """把預算掛到這一輪的所有工具 wrapper；回傳掛上幾個。"""
    count = 0
    for tool in tools or []:
        if isinstance(tool, _CompactingToolWrapper):
            tool.set_budget(budget)
            count += 1
    return count


def build_read_more_tool(
    budget: TurnOutputBudget,
    *,
    requester_id: Optional[str],
    workspace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Any:
    """分頁續讀工具（本輪臨時掛上；不在 registry／DB，只讀本人這個 session 存的結果）。"""
    from langchain_core.tools import tool as lc_tool

    @lc_tool("read_more_result")
    def read_more_result(ref: str, offset: int = 0) -> str:
        """續讀被分頁的工具輸出。ref 與 offset 照上一個結果最後面的提示填。已足夠回答就不要呼叫。"""
        ref_s = str(ref).strip()
        data = retrieve_tool_result(
            ref_s,
            requester_id=requester_id,
            workspace_id=workspace_id,
            session_id=session_id,
        )
        if data.startswith("[ERROR]"):
            return data
        try:
            start = max(0, int(offset))
        except (TypeError, ValueError):
            start = 0
        if start >= len(data):
            return "已經讀完了，沒有更多內容。"
        page, cut = page_text(data, start, budget.allowance())
        budget.consume(len(page))
        return page + page_footer(ref_s, start, cut, len(data))

    return read_more_result


class _CompactingToolWrapper(BaseTool):
    """
    Proxy that compacts large `invoke()` outputs without mutating the original tool.

    Inherits from BaseTool so that isinstance(wrapper, BaseTool) returns True.
    This prevents LangGraph's internal tool() decorator from trying to re-wrap it.
    """

    name: str = ""
    description: str = ""

    # Internal state
    _original: Any = None
    _owner_id: Optional[str] = None
    _workspace_id: Optional[str] = None
    _session_id: Optional[str] = None
    _last_stat: Optional[dict] = None
    _risk_level: str = "low"  # Consent Gate：high-risk tool 呼叫時記 audit
    _budget: Optional[TurnOutputBudget] = None  # 本輪輸出預算（None＝舊行為）

    def __init__(
        self,
        original: Any,
        owner_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        risk_level: str = "low",
        **kwargs: Any,
    ) -> None:
        # Delegate name/description/args_schema from original tool.
        # Use str() to handle MagicMock/other non-string types gracefully.
        name = getattr(original, "name", None)
        desc = getattr(original, "description", None)
        kwargs["name"] = str(name) if name else "unknown"
        kwargs["description"] = str(desc) if desc else ""
        # 🔑 必須繼承 args_schema，否則 Pydantic 從 _run(self, *args, **kwargs)
        # 推導出錯誤的 input_schema（args:array + kwargs:object），讓 LLM 看不到
        # 正確的參數名（如 ticker），導致工具呼叫永遠失敗、LLM 陷入死循環。
        original_args_schema = getattr(original, "args_schema", None)
        if original_args_schema is not None:
            try:
                from pydantic import BaseModel

                if isinstance(original_args_schema, type) and issubclass(
                    original_args_schema, BaseModel
                ):
                    kwargs["args_schema"] = original_args_schema
            except (TypeError, asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass  # MagicMock 等非合法 schema → 跳過，沿用 BaseTool 預設
        super().__init__(**kwargs)
        object.__setattr__(self, "_original", original)
        object.__setattr__(self, "_owner_id", owner_id)
        object.__setattr__(self, "_workspace_id", workspace_id)
        object.__setattr__(self, "_session_id", session_id)
        object.__setattr__(self, "_last_stat", None)
        object.__setattr__(self, "_risk_level", risk_level)
        object.__setattr__(self, "_budget", None)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._original, item)

    @property
    def last_stat(self) -> Optional[dict]:
        return self._last_stat

    @last_stat.setter
    def last_stat(self, value: Optional[dict]) -> None:
        object.__setattr__(self, "_last_stat", value)

    def invoke(self, input: Any, config: Optional[Any] = None, **kwargs: Any) -> Any:  # noqa: A002
        _audit_if_high_risk(self)
        start = time.monotonic()
        try:
            invoke_kwargs = dict(kwargs)
            if config is not None:
                invoke_kwargs["config"] = config
            raw = self._original.invoke(input, **invoke_kwargs)
            text = _to_str(raw)
            latency_ms = int((time.monotonic() - start) * 1000)
            object.__setattr__(
                self,
                "_last_stat",
                {
                    "tool_name": getattr(self._original, "name", "unknown"),
                    "success": True,
                    "latency_ms": latency_ms,
                    "output_chars": len(text),
                    "error_type": None,
                },
            )
            return self._finalize(raw, text)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            object.__setattr__(
                self,
                "_last_stat",
                {
                    "tool_name": getattr(self._original, "name", "unknown"),
                    "success": False,
                    "latency_ms": latency_ms,
                    "output_chars": 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    def set_budget(self, budget: Optional["TurnOutputBudget"]) -> None:
        """掛上本輪的輸出預算（agent 執行前由 execute_streaming 設定）；None＝舊行為（2000 字預覽）。"""
        object.__setattr__(self, "_budget", budget)

    def _finalize(self, raw: Any, text: str) -> Any:
        """工具輸出的收尾：沒超過額度原樣回；超過才壓縮＋存起來。

        - 沒掛預算（舊路徑、測試）：額度 THRESHOLD(2000)，超過走 ``_smart_preview`` 截斷預覽。
        - 掛了預算（claw 的串流路徑）：額度 ＝ 本輪還剩多少 context 可以放工具輸出；超過額度**不是丟掉**，
          而是回「前一頁」＋明確的續讀指引（read_more_result），模型要看後面就分頁讀。
        必須保持與 ``raw`` 同型別，否則 LangGraph 1.x 的 ToolNode 會拋 "Tool X returned unexpected type"。
        """
        budget = getattr(self, "_budget", None)
        if budget is None:
            if len(text) <= THRESHOLD:
                return raw
            uid = _store_sync(
                text,
                owner_id=self._owner_id,
                workspace_id=self._workspace_id,
                session_id=self._session_id,
            )
            logger.debug("[ToolCompactor] stored %s (%d chars)", uid, len(text))
            return self._rewrap_output(raw, _smart_preview(text))
        limit = budget.allowance()
        if len(text) <= limit:
            budget.consume(len(text))
            return raw
        ref = uuid.uuid4().hex[:10]
        _store_sync(
            text,
            owner_id=self._owner_id,
            workspace_id=self._workspace_id,
            session_id=self._session_id,
            uid=ref,
        )
        page, cut = page_text(text, 0, limit)
        budget.consume(len(page))
        logger.info(
            "[ToolCompactor] %s paged: %d chars → first %d (ref=%s)",
            getattr(self._original, "name", "?"),
            len(text),
            len(page),
            ref,
        )
        return self._rewrap_output(
            raw, page + page_footer(ref, 0, cut, len(text))
        )

    @staticmethod
    def _rewrap_output(raw: Any, new_text: str) -> Any:
        """
        將壓縮後文字塞回 raw 的容器型別。
        - raw 是 ToolMessage → 複製一份，改 content
        - raw 是 dict（含 'content'）→ 複製改 content
        - 其他 → 直接回 new_text（字串）

        這確保 LangGraph 收到的型別跟 raw 一致，不會在 ToolNode 報
        "Tool X returned unexpected type: <class 'str'>"。
        """
        try:
            from langchain_core.messages import ToolMessage

            if isinstance(raw, ToolMessage):
                # 保留 ToolMessage 結構（name / tool_call_id 等）
                return ToolMessage(
                    content=new_text,
                    name=raw.name,
                    tool_call_id=raw.tool_call_id,
                    status=getattr(raw, "status", "success"),
                )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

        if isinstance(raw, dict) and "content" in raw:
            new_dict = dict(raw)
            new_dict["content"] = new_text
            return new_dict

        return new_text

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Sync wrapper for invoke(). Required by BaseTool abstract method."""
        return self.invoke(*args, **kwargs)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Async wrapper for ainvoke(). Delegates to _original's ainvoke or invoke."""
        ainvoke = getattr(self._original, "ainvoke", None)
        if ainvoke is not None:
            return await ainvoke(*args, **kwargs)
        return self.invoke(*args, **kwargs)

    async def ainvoke(
        self, input: Any, config: Optional[Any] = None, **kwargs: Any
    ) -> Any:  # noqa: A002
        """Async 版本同樣需要做 compaction，避免長輸出爆 LLM context。"""
        _audit_if_high_risk(self)
        invoke_kwargs = dict(kwargs)
        if config is not None:
            invoke_kwargs["config"] = config

        ainvoke = getattr(self._original, "ainvoke", None)
        if ainvoke is None:
            return self.invoke(input, config=config, **kwargs)

        start = time.monotonic()
        try:
            raw = await ainvoke(input, **invoke_kwargs)
            text = _to_str(raw)
            latency_ms = int((time.monotonic() - start) * 1000)
            object.__setattr__(
                self,
                "_last_stat",
                {
                    "tool_name": getattr(self._original, "name", "unknown"),
                    "success": True,
                    "latency_ms": latency_ms,
                    "output_chars": len(text),
                    "error_type": None,
                },
            )
            return self._finalize(raw, text)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            object.__setattr__(
                self,
                "_last_stat",
                {
                    "tool_name": getattr(self._original, "name", "unknown"),
                    "success": False,
                    "latency_ms": latency_ms,
                    "output_chars": 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise


def _compact_output(
    raw: Any,
    *,
    owner_id: Optional[str],
    workspace_id: Optional[str],
    session_id: Optional[str],
) -> Any:
    text = _to_str(raw)
    if len(text) <= THRESHOLD:
        return raw
    uid = _store_sync(
        text,
        owner_id=owner_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )
    logger.debug("[ToolCompactor] stored %s (%d chars)", uid, len(text))
    compacted_text = _smart_preview(text)
    # 保持與 raw 同型別（避免 LangGraph ToolNode 抱怨 unexpected type）
    return _CompactingToolWrapper._rewrap_output(raw, compacted_text)


def build_tool_guard_consent_payload(tool_name: str, user_id) -> dict:
    """per-tool-call guard 的 consent payload。

    type 必須是 ``consent_gate``——前端 chat-analysis.js 的 hitl_question
    分派只認這個值（舊碼用 "consent" 會掉進 clarify fallback，顯示
    「請問您具體想了解什麼？」，與高風險同意語境不符）。
    """
    return {
        "type": "consent_gate",
        "tool_name": tool_name,
        "user_id": user_id,
        "message": (
            f"High-risk tool「{tool_name}」即將執行（per-tool-call guard）。"
            "請確認是否允許。"
        ),
    }


def _audit_if_high_risk(wrapper: "_CompactingToolWrapper") -> None:
    """high-risk tool 被呼叫時記 audit + （啟用時）執行前攔截。

    兩道防線演進：
    - **原本**：只有事後稽核（log_high_risk_tool_execution），consent gate 的
      pre-execution 攔截是唯一事前防線。
    - **Phase 0 升級（本函式）**：加 per-tool-call 攔截，作為 consent gate 的
      defense-in-depth。當 CONSENT_TOOL_GUARD_ENABLED=true 時，high-risk tool
      執行前呼叫 interrupt() 暫停——這層不管呼叫者是 parent agent 還是 sub-agent
      （未來 Deep Agents 的 task 子 agent）都會觸發，補 consent gate 看不到
      sub-agent 決定的漏洞。

    觸發 interrupt 需要 graph 有 checkpointer（resume 用）。無 checkpointer 時
    （如測試環境）退回純稽核——graceful，不阻塞 tool 執行。

    interrupt 的 payload 走既有 consent_gate 格式（見 build_tool_guard_consent_payload），
    前端 renderConsentCard 渲染。resume 後：使用者同意 → 繼續執行；拒絕 → 拋 ToolException 中止。
    """
    try:
        if getattr(wrapper, "_risk_level", "low") != "high":
            return
        tool_name = getattr(wrapper._original, "name", "unknown")
        user_id = getattr(wrapper, "_owner_id", None)

        # 1. 事後稽核（永遠做，不管攔截是否啟用）
        from core.agents.manager.consent_gate import log_high_risk_tool_execution

        log_high_risk_tool_execution(user_id=user_id, tool_name=tool_name)

        # 2. Phase 0：per-tool-call 攔截（defense-in-depth，預設關閉）
        #    啟用條件：CONSENT_TOOL_GUARD_ENABLED=true（明確 opt-in）。
        #    預設關閉是因為：interrupt 需要 checkpointer，而內層 agent 目前沒有
        #    （Phase 1 遷移 Deep Agents 時會補）。補上 checkpointer 後啟用此 guard
        #    才有意義。啟用子 agent（Phase 3）前必須先啟用此 guard。
        from core.feature_flags import env_flag

        if not env_flag("CONSENT_TOOL_GUARD_ENABLED"):
            return

        from langgraph.types import interrupt

        payload = build_tool_guard_consent_payload(tool_name, user_id)
        answer = interrupt(payload)
        # resume：answer 是使用者的決定。非明確同意 → 中止。
        approved = (
            isinstance(answer, str)
            and answer.strip().lower() in ("yes", "y", "ok", "同意", "approve", "1")
        ) or (
            isinstance(answer, dict) and answer.get("approved") is True
        )
        if not approved:
            from langchain_core.tools import ToolException

            raise ToolException(
                f"High-risk tool「{tool_name}」被使用者拒絕（per-tool-call guard），已取消。"
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except ImportError:
        # langgraph.types 或 langchain_core.tools 不可用（測試環境）→ 退回純稽核
        pass
    except Exception as exc:
        # 必須原樣上拋的 control-flow 例外（不能被吞）：
        from langchain_core.tools import ToolException
        from langgraph.errors import GraphInterrupt

        if isinstance(exc, (GraphInterrupt, ToolException)):
            # - GraphInterrupt：interrupt() 首次暫停時拋，必須上拋讓 graph 暫停
            #   （同 claw_loop 的 except GraphInterrupt: raise）。吞掉 = guard 失效。
            # - ToolException：使用者拒絕 consent 時拋，ToolNode 會轉成 error
            #   ToolMessage 讓 agent 知道工具被取消。吞掉 = 拒絕無效、工具靜默執行。
            raise
        # 其他故障（稽核 log 故障等）不阻塞 tool 執行（graceful）。
        pass


def wrap_tool(
    tool: Any,
    owner_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    risk_level: str = "low",
) -> Any:
    """Return a non-mutating wrapper for LangChain tools that expose `invoke()`.

    risk_level 由 base_react_agent._get_tool_metas 從 ToolMetadata 帶入（預設 low）。
    high-risk tool 被呼叫時，wrapper 會記 audit（事後追溯防線）。
    """
    if not hasattr(tool, "invoke"):
        return tool
    if _is_compactor_wrapped(tool):
        return tool
    return _CompactingToolWrapper(
        tool,
        owner_id=owner_id,
        workspace_id=workspace_id,
        session_id=session_id,
        risk_level=risk_level,
    )


def retrieve_tool_result(
    uid: str,
    requester_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    record = _retrieve_sync(uid)
    if record is None:
        return f"[ERROR] Tool result '{uid}' not found or expired."
    owner_id, owner_scope, data = record
    # 沒有查詢者（訪客 agent，user_id=None）不得讀有主人的結果。訪客白名單本來就排除
    # tool_result_retrieve，這裡是第二層：下面兩個比對在 requester_id 為 None 時都會跳過。
    if requester_id is None and owner_id is not None:
        return f"[ERROR] Tool result '{uid}' is not available for this user."
    requester_scope = None
    if requester_id is not None:
        requester_scope = scope_namespace(
            build_scope(requester_id, session_id=session_id, workspace_id=workspace_id)
        )
    if (
        requester_scope is not None
        and owner_scope is not None
        and requester_scope != owner_scope
    ):
        return f"[ERROR] Tool result '{uid}' is not available for this user."
    if (
        requester_scope is None
        and requester_id is not None
        and owner_id is not None
        and requester_id != owner_id
    ):
        return f"[ERROR] Tool result '{uid}' is not available for this user."
    return data


def _reset_for_testing() -> None:
    """Reset module-level state used by tests."""
    global _redis_client, _redis_init_attempted
    _redis_client = None
    _redis_init_attempted = False
    _local_store.clear()
    _wrapped_tool_ids.clear()
    _tool_stats.clear()
