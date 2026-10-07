"""
BYOK Fallback Provider Chain — 骨架（預設停用）。

背景
----
Hermes-Agent 在 LLM 持續空回時會切到 fallback provider/model。CryptoMind 是
BYOK 多 provider 架構，理論上很適合加這層：當使用者的 BYOK 模型爛到不行
（例如免費額度的開源模型持續回空），自動 fallback 到 server-side 預設模型
完成這次請求。

但這涉及 **server-side 成本**（fallback 用 server key，等於免費借用資源），
屬於產品決策。本模組只實作骨架，預設停用。未來 DANNY 確認成本/體驗 trade-off
後，只需設定 ``BYOK_FALLBACK_ENABLED=true`` + ``BYOK_FALLBACK_PROVIDER`` +
``BYOK_FALLBACK_MODEL`` + ``BYOK_FALLBACK_API_KEY`` 即可啟用。

設計
----
- ``should_fallback(result, retry_counter)``：判斷要不要 fallback
- ``get_fallback_client()``：從 env 組 fallback client（None = 未啟用）
- 整合點：``claw_loop._claw_loop_node`` 在 ``execute_streaming`` 失敗後檢查

未啟用時，所有函式 no-op，不影響任何現有流程。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Cascade-loop 防護（學 Hermes fallback-providers doc + issue #24996）────────
# Hermes 早期沒有「每輪最多一次」限制時，多 provider 同時非重試失敗會
# tight-loop 重 marshalling 80k token context 燒爆記憶體。
#
# 設計（turn-scoped + at most once per turn）：
# - 每個「turn」（一個使用者訊息 = 一次 claw_loop 執行）最多 fallback 1 次。
# - 跨 turn 的短時間內（CASCADE_WINDOW_SECONDS）累計超 MAX_CASCADE_FALLBACKS
#   → 暫時熔斷（認定 fallback provider 本身有問題，不再燒錢）。
MAX_FALLBACKS_PER_TURN = 1
MAX_CASCADE_FALLBACKS = 3  # 窗口內累計上限
CASCADE_WINDOW_SECONDS = 60  # 熔斷窗口

# 模組級計數器。claw_loop 每個請求開始時呼叫 reset_turn_fallback_guard()。
_turn_fallback_count = 0
_cascade_timestamps: list[float] = []


def reset_turn_fallback_guard() -> None:
    """每個使用者訊息（turn）開始時重設 per-turn 計數器。

    claw_loop._claw_loop_node 開頭呼叫。對齊 Hermes「每個新 user message 都從
    主模型重來，fallback 每輪重置」。
    """
    global _turn_fallback_count
    _turn_fallback_count = 0


def _cascade_tripped() -> bool:
    """窗口內 fallback 累計是否超上限（熔斷）。"""
    now = time.monotonic()
    # 清掉過期時間戳
    cutoff = now - CASCADE_WINDOW_SECONDS
    global _cascade_timestamps
    _cascade_timestamps = [ts for ts in _cascade_timestamps if ts > cutoff]
    return len(_cascade_timestamps) >= MAX_CASCADE_FALLBACKS


def _record_fallback() -> None:
    """記錄一次 fallback（供 cascade 計數）。"""
    global _turn_fallback_count, _cascade_timestamps
    _turn_fallback_count += 1
    _cascade_timestamps.append(time.monotonic())


def _entry(suffix: str) -> Optional[dict]:
    """讀一組 BYOK_FALLBACK{suffix}_PROVIDER / _MODEL / _API_KEY。provider 沒設回 None。"""
    from core.model_config import (
        LOCAL_LLAMA_DEFAULT_MODEL,
        get_provider_runtime,
        is_local_provider,
        resolve_server_api_key,
    )

    provider = os.getenv(f"BYOK_FALLBACK{suffix}_PROVIDER", "").strip().lower()
    if not provider or not get_provider_runtime(provider):
        return None
    api_key = os.getenv(
        f"BYOK_FALLBACK{suffix}_API_KEY", ""
    ).strip() or resolve_server_api_key(provider)
    if not api_key:
        return None
    model = os.getenv(f"BYOK_FALLBACK{suffix}_MODEL", "").strip() or None
    if is_local_provider(provider):
        model = model or LOCAL_LLAMA_DEFAULT_MODEL
    return {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "local": is_local_provider(provider),
    }


def fallback_chain() -> list[dict]:
    """平台金鑰鏈：BYOK_FALLBACK_*（主）→ BYOK_FALLBACK_2_*（備，可不設）。
    2026-09-22 DANNY：主 = local_llama（本地 NeoHorse，零成本）；雲端備援要花錢，生產不設。"""
    chain = [e for e in (_entry(""), _entry("_2")) if e]
    seen: set[tuple] = set()
    out = []
    for e in chain:
        key = (e["provider"], e["model"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def is_fallback_enabled() -> bool:
    """Fallback 是否啟用。

    啟用條件（全部滿足）：
    1. ``BYOK_FALLBACK_ENABLED=true``
    2. 鏈裡至少一組 provider 設定完整（local_llama 不需要 key）
    3. 非 TEST_MODE（避免測試意外打到 fallback）
    """
    if os.getenv("TEST_MODE", "false").lower() in {"true", "1", "yes"}:
        return False
    from core.feature_flags import env_flag

    if not env_flag("BYOK_FALLBACK_ENABLED", "false"):
        return False
    return bool(fallback_chain())


# ── 本地 llama-server 健康探測（快取 20 秒；主機重開機時 :8080 會掉，要能自動切雲端）──
LOCAL_HEALTH_TTL_SECONDS = 20
_local_health: dict[str, tuple[float, bool]] = {}


def local_provider_healthy(provider: str, force: bool = False) -> bool:
    """GET <base_url 去掉 /v1>/health，2 秒逾時；非 local provider 一律 True。"""
    from core.model_config import get_provider_runtime, is_local_provider

    if not is_local_provider(provider):
        return True
    runtime = get_provider_runtime(provider) or {}
    base = str(runtime.get("base_url") or "").rstrip("/")
    if not base:
        return False
    now = time.monotonic()
    cached = _local_health.get(base)
    if cached and not force and now - cached[0] < LOCAL_HEALTH_TTL_SECONDS:
        return cached[1]
    url = (base[: -len("/v1")] if base.endswith("/v1") else base) + "/health"
    ok = False
    try:
        import httpx

        resp = httpx.get(url, timeout=2.0, trust_env=False)
        ok = (
            resp.status_code == 200
            and str(resp.json().get("status", "")).lower() == "ok"
        )
    except Exception as exc:  # noqa: BLE001 — 探測失敗就是不健康
        logger.info("[Fallback] local llama health probe failed (%s): %s", url, exc)
        ok = False
    _local_health[base] = (now, ok)
    return ok


def platform_model_enabled() -> bool:
    """平台模型（keyless provider）有沒有啟用——只看 env，不探測（給 repo 合成綁定用）。"""
    return is_fallback_enabled() and any(e["local"] for e in fallback_chain())


def pick_fallback(prefer_cloud: bool = False) -> Optional[dict]:
    """從鏈裡挑第一個可用的：本地要過健康探測；prefer_cloud=True 先看雲端（進階會員／救援）。"""
    chain = fallback_chain()
    if prefer_cloud:
        chain = [e for e in chain if not e["local"]] + [e for e in chain if e["local"]]
    for e in chain:
        if e["local"] and not local_provider_healthy(e["provider"]):
            continue
        return e
    return None


DEFAULT_FALLBACK_DAILY_LIMIT = 10


def fallback_daily_limit() -> int:
    """沒自帶金鑰、走平台金鑰的人每日上限（``BYOK_FALLBACK_DAILY_LIMIT``，預設 10）。

    只套在「花錢的」平台雲端 key（deepseek 等）；本地 local_llama 不套（零成本）。
    免費層另有 FREE_DAILY_CHAT_LIMIT 先擋；這個上限主要管「Premium 但沒綁金鑰」的人
    ——形同無限，但擋腳本濫用（2026-09-22 生產設 200）。
    """
    from core.setting_overrides import param_raw  # 後台覆寫 > env

    raw = param_raw("BYOK_FALLBACK_DAILY_LIMIT")
    try:
        value = int(raw) if raw else DEFAULT_FALLBACK_DAILY_LIMIT
    except ValueError:
        return DEFAULT_FALLBACK_DAILY_LIMIT
    return max(0, value)


def fallback_credentials(tier: str = "free") -> Optional[dict]:
    """沒有使用者金鑰時的平台憑證（2026-09-12 留存核心第 5 項：免費配額）。

    2026-09-22 DANNY：**所有等級都走本地模型**（零成本）——進階會員也不給雲端 key，
    差別只在免費會員有每日上限、進階不限。鏈上若設了雲端備援（BYOK_FALLBACK_2_*），
    只在本地掛掉時才用。回傳多帶 ``fallback=True``（呼叫端套日額）與 ``local``
    （本地不計平台金鑰日額）。未啟用回 None（現行為：400 要使用者去設定金鑰）。
    ``tier`` 目前只留作介面（呼叫端已傳），不影響挑選順序。
    """
    if not is_fallback_enabled():
        return None
    entry = pick_fallback(prefer_cloud=False)
    if not entry:
        return None
    return {**entry, "fallback": True}


def should_fallback(
    final_response: Optional[str],
    used_tools: list,
    retry_exhausted: bool,
    error: Optional[BaseException] = None,
) -> bool:
    """判斷是否該走 fallback。

    觸發條件（AND）：
    1. ``is_fallback_enabled()`` 為 True
    2. ``retry_exhausted`` 為 True（recovery 都試過了）
       **或** error 是 capacity error（402/quota —— 主模型配額耗盡，retry 無
       意義，該換 provider，學 Hermes fallback-providers doc）
    3. ``final_response`` 是空 or 是 fallback 訊息（例如「模型沒有產生回應」）
    4. 本 turn 還沒 fallback 過（turn-scoped at most once，防 cascade loop）
    5. 熔斷未觸發（窗口內累計未超 MAX_CASCADE_FALLBACKS）

    Args:
        final_response: claw_loop 的最終回應。
        used_tools: 本次用過的工具（資訊用，目前不影響判斷）。
        retry_exhausted: RetryCounter 是否已耗盡。
        error: 觸發失敗的例外（若有）。capacity error 直接觸發 fallback。

    Returns:
        True 若該 fallback。
    """
    if not is_fallback_enabled():
        return False
    # turn-scoped：每輪最多一次
    if _turn_fallback_count >= MAX_FALLBACKS_PER_TURN:
        return False
    # cascade 熔斷
    if _cascade_tripped():
        logger.warning(
            "[Fallback] cascade 熔斷：%ds 內已 fallback %d 次，暫停 fallback",
            CASCADE_WINDOW_SECONDS,
            MAX_CASCADE_FALLBACKS,
        )
        return False
    # 觸發條件：retry 耗盡 OR capacity error（402/quota 該換 provider）
    from .rate_limit import is_capacity_error

    capacity_triggered = error is not None and is_capacity_error(error)
    if not retry_exhausted and not capacity_triggered:
        return False
    if final_response and len(final_response) > 50:
        # 有實質內容（>50 字）就不 fallback
        return False
    return True


def mark_fallback_used() -> None:
    """fallback 執行後呼叫，記錄計數（供 turn-scoped + cascade）。

    claw_loop 在實際跑完 fallback client 後呼叫。
    """
    _record_fallback()


def get_fallback_client() -> Optional[Any]:
    """從 env 組 fallback LLM client。

    Fallback 用較小的 max_tokens（CHAT_MAX_OUTPUT_TOKENS_FALLBACK=6144），
    保留預扣緩衝避免 402；fallback 模型通常用 server-side 較強模型，不需要 8K。

    Returns:
        LangChain LLM client，或 None（未啟用 / 設定不完整）。
    """
    if not is_fallback_enabled():
        return None
    try:
        from utils.user_client_factory import (
            CHAT_MAX_OUTPUT_TOKENS_FALLBACK,
            create_user_llm_client,
        )

        # 救援用（使用者 BYOK 模型連續空回）：雲端優先——30 秒硬上限對 iGPU 上的 9B 太緊
        entry = pick_fallback(prefer_cloud=True)
        if not entry:
            return None
        return create_user_llm_client(
            provider=entry["provider"],
            api_key=entry["api_key"],
            model=entry["model"],
            max_tokens=CHAT_MAX_OUTPUT_TOKENS_FALLBACK,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to build fallback client: %s", exc)
        return None


# Fallback provider 呼叫的硬上限（秒）。
# Hermes #12770 教訓：cascade fallback 易浪費 20-60s。加 30s 上限，
# 超過就直接放棄 fallback，走既有的 _no_valid_result_message。
FALLBACK_TIMEOUT_SECONDS = 30


__all__ = [
    "is_fallback_enabled",
    "fallback_chain",
    "fallback_credentials",
    "fallback_daily_limit",
    "pick_fallback",
    "local_provider_healthy",
    "should_fallback",
    "get_fallback_client",
    "reset_turn_fallback_guard",
    "mark_fallback_used",
    "FALLBACK_TIMEOUT_SECONDS",
    "MAX_FALLBACKS_PER_TURN",
    "MAX_CASCADE_FALLBACKS",
    "CASCADE_WINDOW_SECONDS",
]
