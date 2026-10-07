"""訪客模式 API — 免登入體驗 AI 市場分析（設計：docs/plans/2026-08-19-guest-access-design.md）。

邊界（與主 /api/analyze 完全分離，主流程零風險）：
- 訪客身分：簽名 cookie ``guest_id``（uuid + HMAC），30 天，不建 user 列（零 schema）
- 每日限量：``GUEST_DAILY_QUESTIONS``（預設 3/天，UTC 日），Redis shared_cache 計數
- 全站每日總量：``GUEST_GLOBAL_DAILY_CAP``（預設 300/天，0=關）— 防「清 cookie 換新
  身分」輪替打爆平台 OpenRouter 免費線日額（DANNY 2026-08-20 決策）
- 回答（2026-09-27 上市準備 PR-4）：跟免費會員同一個 agent（core/agents/guest_agent.py），
  只開唯讀市場工具、多輪（前端帶最近 6 則，伺服器不存）；agent 出錯或超過 60 秒就退回單次
  回答。``GUEST_AGENT_ENABLED=false`` 直接走單次回答（一鍵回滾）
- 模型：免費會員同款平台模型（fallback_credentials）優先，再來是訪客鏈
  （env ``GUEST_LLM_PROVIDER``/``GUEST_LLM_MODEL``/``GUEST_LLM_FALLBACK_MODELS``）
- 高風險功能一律不開放：本 router 只有聊天，其餘路由照舊 get_current_user fail-closed
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
import openai
from fastapi import APIRouter, HTTPException, Request, Response
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.exceptions import LangChainException
from langgraph.errors import InvalidUpdateError
from pydantic import BaseModel, Field

from api import funnel
from api.deps import set_site_cookie
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core import shared_cache
from core.i18n import t
from core.validators.content_filter import filter_chat_message
from utils.llm_client import LLMClientFactory
from utils.user_client_factory import create_user_llm_client

router = APIRouter()

GUEST_COOKIE = "guest_id"
_GUEST_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 天
_DEFAULT_DAILY = 3
# 全站訪客每日總量上限（與每人限量同一套 Redis/local 計數模式）。
# 用意：每人限量靠 cookie 可被「清 cookie 換身分」繞過，全站計數才是
# 保護平台 OpenRouter 免費日額的硬上限；0 = 關閉（僅留每人限量）。
_DEFAULT_GLOBAL_DAILY = 300
# 訪客模型：2026-09-22 DANNY 改成平台自架本地模型優先（local_llama / NeoHorse，零成本、
# 不吃 OpenRouter 免費線日額），備援鏈 GUEST_LLM_FALLBACK_MODELS 可寫 "provider:model"
# （例如 "openrouter:nvidia/nemotron-3-super-120b-a12b:free"），沒寫 provider 就沿用主 provider。
# 2026-08-19：deepseek-r1:free 已退場（OpenRouter 404 "unavailable for free"）。
_DEFAULT_PROVIDER = "local_llama"
_DEFAULT_MODEL = "neohorse-1-9b"
_DEFAULT_FALLBACK_MODELS = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"

# 匿名化：log 只留 guest_id 前 8 碼
_GUEST_LOG_PREFIX_LEN = 8

# 時間預算：整題 90 秒；agent 最多 60 秒，剩下的留給單次回答備援
_TOTAL_BUDGET_SECONDS = 90.0
_AGENT_BUDGET_SECONDS = 60.0
# agent 路徑的輸出上限（平台模型 client 建立時帶入；單次回答沿用 invoke 時的 1200）
_AGENT_MAX_TOKENS = 2048

# 多輪：前端帶最近幾則、每則多長（伺服器不存）
_HISTORY_MAX_ITEMS = 6
_HISTORY_CONTENT_MAX = 2000

_LANGUAGE_NAMES = {
    "zh-TW": "繁體中文",
    "zh-CN": "简体中文",
    "en": "English",
    "ru": "Русский",
}

# 預期內的失敗：逾時、連線、供應商 SDK、graph／呼叫上限、輸出解析、資料處理。
# 不用 except Exception（AGENTS.md）；CancelledError 是 BaseException，不會被這組吃掉。
_EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    RuntimeError,
    httpx.HTTPError,
    openai.OpenAIError,
    LangChainException,
    InvalidUpdateError,
    ModelCallLimitExceededError,
    ToolCallLimitExceededError,
)


def _guest_secret() -> str:
    """訪客 cookie 簽名金鑰 — 與 JWT 同源（api.deps 同款讀法）。"""
    from api.deps import SECRET_KEY

    return SECRET_KEY


def _sign(value: str) -> str:
    return hmac.new(
        _guest_secret().encode(), value.encode(), hashlib.sha256
    ).hexdigest()[:16]


def _issue_guest_id() -> str:
    raw = uuid.uuid4().hex
    return f"{raw}.{_sign(raw)}"


def _valid_guest_id(value: str) -> bool:
    if not value or "." not in value:
        return False
    raw, _, sig = value.rpartition(".")
    return bool(re.fullmatch(r"[0-9a-f]{32}", raw)) and hmac.compare_digest(
        _sign(raw), sig
    )


def _resolve_guest(request: Request, response: Response) -> str:
    """讀取或發放訪客 id（簽名 cookie）。無效簽名一律換新（不信任舊值）。"""
    value = request.cookies.get(GUEST_COOKIE, "")
    if not _valid_guest_id(value):
        value = _issue_guest_id()
        # 屬性與 auth cookie 一致（production SameSite=None; Partitioned）：
        # mini app 在 iframe 裡的訪客配額才會跟著同一顆 cookie，不會每問一次換新 id
        set_site_cookie(response, GUEST_COOKIE, value, _GUEST_COOKIE_MAX_AGE)
        funnel.record_guest_first_seen(value)  # 轉換漏斗：新訪客（PR-5）
    return value


def _daily_limit() -> int:
    from core.setting_overrides import param_raw  # 後台覆寫 > env

    raw = param_raw("GUEST_DAILY_QUESTIONS")
    try:
        n = int(raw) if raw else _DEFAULT_DAILY
    except ValueError:
        n = _DEFAULT_DAILY
    return max(0, n)


def guest_data_access_enabled() -> bool:
    """Tier 1（docs/plans/2026-08-27-guest-mode-tier1-design.md）：
    訪客唯讀市場數據總開關——即時讀 env，off 可一鍵回滾。"""
    return os.getenv("GUEST_DATA_ACCESS", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def guest_agent_enabled() -> bool:
    """PR-4：訪客走真正的 agent（預設開）；false＝一律單次回答（線上出事一鍵回滾）。"""
    from core.feature_flags import env_flag  # noqa: PLC0415

    return env_flag("GUEST_AGENT_ENABLED", "true")


def _global_cap() -> int:
    from core.setting_overrides import param_raw  # 後台覆寫 > env

    raw = param_raw("GUEST_GLOBAL_DAILY_CAP")
    try:
        n = int(raw) if raw else _DEFAULT_GLOBAL_DAILY
    except ValueError:
        n = _DEFAULT_GLOBAL_DAILY
    return max(0, n)


# 模型鏈：主模型（GUEST_LLM_MODEL，預設 nemotron 免費線）＋備用
# （GUEST_LLM_FALLBACK_MODELS，逗號分隔）。免費線日額（帳號級）耗盡或
# 模型退場時自動切備用——訪客模式是行銷入口，不該因單一模型掛掉而整個 502。
def _model_chain() -> list[tuple[str, str]]:
    """回傳 [(provider, model), ...]。主模型 = GUEST_LLM_PROVIDER/GUEST_LLM_MODEL，
    備援 = GUEST_LLM_FALLBACK_MODELS（逗號分隔；"provider:model" 或純 model）。"""
    from core.model_config import get_provider_runtime  # noqa: PLC0415

    primary_provider = (
        (os.getenv("GUEST_LLM_PROVIDER") or _DEFAULT_PROVIDER).strip().lower()
    )
    primary = (os.getenv("GUEST_LLM_MODEL") or _DEFAULT_MODEL).strip()
    raw = os.getenv("GUEST_LLM_FALLBACK_MODELS")
    if raw is None:
        raw = _DEFAULT_FALLBACK_MODELS
    chain: list[tuple[str, str]] = [(primary_provider, primary)]
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        head, sep, rest = item.partition(":")
        if sep and get_provider_runtime(head.strip().lower()):
            chain.append((head.strip().lower(), rest.strip()))
        else:
            chain.append((primary_provider, item))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pm in chain:
        if pm[1] and pm not in seen:
            seen.add(pm)
            out.append(pm)
    return out


# 視為「模型額度/可用性」問題的錯誤特徵——這類才值得燒備用鏈重試；
# 其他（網路瞬斷、輸出壞掉）重試別的模型也沒意義，直接上拋。
# 本地 llama-server 例外：主機重開機它就掉（連線錯誤／逾時），一律換下一個。
_QUOTA_ERROR_MARKERS = ("429", "404", "rate limit", "unavailable", "not found", "quota")
_LOCAL_ERROR_MARKERS = ("connect", "connection", "timeout", "timed out", "503", "502")


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)


def _is_switchable_error(exc: Exception, provider: str) -> bool:
    from core.model_config import is_local_provider  # noqa: PLC0415

    if _is_quota_error(exc):
        return True
    if is_local_provider(provider):
        text = str(exc).lower()
        return isinstance(exc, (TimeoutError, ConnectionError)) or any(
            m in text for m in _LOCAL_ERROR_MARKERS
        )
    return False


def _quota_key(guest_id: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw = guest_id.rpartition(".")[0]
    return f"guest_quota:{raw}:{day}"


def _global_key() -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"guest_quota:global:{day}"


# 進程內 fallback：shared_cache 無 Redis 時是靜默 no-op（get 永遠 None），
# 配額會形同虛設。這裡用本地 dict 兜底 —— 生產多 worker 下 Redis 命中（正確），
# 單機/測試/Redis 故障時至少單進程內限量仍成立（fail-safe 而非 fail-open）。
_local_quota: dict[str, int] = {}


def _quota_used(guest_id: str) -> int:
    key = _quota_key(guest_id)
    remote = shared_cache.get_json(key)
    if remote is not None:
        return int(remote)
    return _local_quota.get(key, 0)


def _quota_remaining(guest_id: str) -> int:
    """剩餘次數 = 限額 − 已用。計數有輕微 race 可容忍（免費額度用途）。"""
    return max(0, _daily_limit() - _quota_used(guest_id))


def _consume_quota(guest_id: str) -> int:
    key = _quota_key(guest_id)
    used = _quota_used(guest_id) + 1
    shared_cache.set_json(key, used, ttl=2 * 86400)
    if shared_cache.get_json(key) is None:  # Redis 沒寫入（不可用）→ 本地兜底
        _local_quota[key] = used
    _global_bump()  # 成功回覆才計全站（LLM 失敗不燒平台日額預算）
    funnel.record_guest_question(guest_id, used)  # 轉換漏斗：今天第幾題（PR-5）
    return max(0, _daily_limit() - used)


_local_global: dict[str, int] = {}


def _global_used() -> int:
    key = _global_key()
    remote = shared_cache.get_json(key)
    if remote is not None:
        return int(remote)
    return _local_global.get(key, 0)


def _global_bump() -> None:
    key = _global_key()
    used = _global_used() + 1
    shared_cache.set_json(key, used, ttl=2 * 86400)
    if shared_cache.get_json(key) is None:
        _local_global[key] = used


def _market_snapshot() -> str:
    """單次回答用的即時行情快照（BTC/ETH 現價 + 24h 漲跌），失敗回空字串（fail-open）。

    agent 路徑有工具，不用這個；TON 已不是產品主軸（2026-09-27 拿掉）。
    """
    from data.market_data import get_klines  # noqa: PLC0415

    lines = []
    for sym in ("BTC", "ETH"):
        try:
            df = get_klines(f"{sym}USDT", exchange="okx", interval="1d", limit=2)
            if df is None or len(df) < 1:
                continue
            last = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2]) if len(df) >= 2 else last
            chg = ((last - prev) / prev * 100) if prev else 0.0
            lines.append(f"- {sym}: ${last:,.2f} ({chg:+.2f}% 24h)")
        except _EXPECTED_ERRORS as exc:  # 單一幣失敗不擋其他
            logger.warning("[Guest] snapshot %s failed: %s", sym, type(exc).__name__)
    return "\n".join(lines)


# 訪客規則（agent 與單次回答共用）。2026-09-27 DANNY：拿掉「叫人連錢包」那條——只有問到
# 自己的持倉／記帳時，才一句話說明登入可以記住持倉；其他時候不提登入、錢包、升級。
# 記帳那句沿用 2026-08-23 的修正：訪客問記帳時模型會幻覺「已記錄」。
# 免責那句只在回答涉及市場／投資時附加：問候、閒聊也附「Not financial advice.」很突兀。
_GUEST_RULES = """## Guest mode (the visitor is not signed in)
1. Answer the market question directly and concisely (aim for under 300 words). If the \
question is too broad to answer (e.g. "is now a good time to invest?"), ask one short \
clarifying question instead of guessing a specific asset.
2. Nothing about this visitor is available or saved here: no portfolio, ledger, memory \
or alerts. Only if they ask about their own holdings, portfolio, trades or expenses \
(e.g. "how is my portfolio?", "record lunch 250", "記一筆 300"): say in one short \
sentence that signing in lets CryptoMind remember their holdings, and never claim \
anything was recorded. Otherwise do not mention signing in, wallets, upgrades or pricing.
3. CryptoMind is a data and analysis tool: it never holds user funds and never executes \
trades. Do NOT offer to buy, sell or transfer anything on the visitor's behalf.
4. Only if your answer discusses a market, an asset's price or an investment decision, \
end it with one line: "Not financial advice." (written in the language of your answer). \
Do not add it to greetings, small talk or questions that are not about markets or investing."""

# agent 路徑：工具取代舊的行情快照
_GUEST_AGENT_RULES = (
    _GUEST_RULES
    + "\n5. Use the tools for live prices, indicators, news and public on-chain checks; "
    "never invent numbers. Tool calls are limited — after a few, answer with what you have."
)

# 單次回答（agent 失敗、逾時，或 GUEST_AGENT_ENABLED=false）：沒有工具，只帶 BTC／ETH 快照
_GUEST_SYSTEM_PROMPT = (
    "You are CryptoMind's market analyst giving a free guest answer in {language}.\n\n"
    + _GUEST_RULES
    + "\n5. You cannot run tools in this reply. Use the live data below if relevant; "
    "do NOT invent prices.\n\nLive data (may be empty):\n{snapshot}"
)


class GuestHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_HISTORY_CONTENT_MAX)


class GuestAnalyzeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    language: Optional[str] = Field(default=None, max_length=10)
    # 多輪（PR-4）：前端帶本頁最近幾則對話；由前端提供＝不可信，只當上下文用
    history: list[GuestHistoryMessage] = Field(
        default_factory=list, max_length=_HISTORY_MAX_ITEMS
    )


class GuestAnalyzeResponse(BaseModel):
    reply: str
    remaining: int
    limit: int


class GuestQuotaResponse(BaseModel):
    limit: int
    remaining: int
    data_access: bool = True


@router.get("/api/guest/quota")
@limiter.limit("30/minute")
async def guest_quota(request: Request, response: Response) -> GuestQuotaResponse:
    """訪客額度查詢（guest banner 顯示每日限額用）：不扣額度、不碰 LLM。"""
    guest_id = _resolve_guest(request, response)
    return GuestQuotaResponse(
        limit=_daily_limit(),
        remaining=_quota_remaining(guest_id),
        data_access=guest_data_access_enabled(),
    )


@router.post("/api/guest/analyze")
@limiter.limit("5/minute")
async def guest_analyze(
    request: Request, response: Response, body: GuestAnalyzeRequest
) -> GuestAnalyzeResponse:
    """訪客 AI 市場分析（免登入、每日限量；免費會員同一個 agent，只開唯讀市場工具）。"""
    guest_id = _resolve_guest(request, response)
    short_id = guest_id[:_GUEST_LOG_PREFIX_LEN]

    # 輸入防護：與主聊天同一套 content filter。歷史由前端帶（可偽造），使用者那幾則也過一次
    for text in [body.message, *(m.content for m in body.history if m.role == "user")]:
        if not filter_chat_message(text)["valid"]:
            raise HTTPException(
                status_code=400, detail="Message could not be processed."
            )

    if _quota_remaining(guest_id) <= 0:
        raise HTTPException(
            status_code=429,
            detail=(
                "Guest daily limit reached. "
                "Log in with an EVM wallet or Telegram for more AI analysis."
            ),
        )

    if _global_cap() and _global_used() >= _global_cap():
        # 個人額度可能還有，但全站日額用罄（防輪替身分打爆平台免費線）
        logger.warning("[Guest] global daily cap hit (%d)", _global_cap())
        raise HTTPException(
            status_code=429,
            detail=(
                "Guest mode is at full capacity today. "
                "Log in with an EVM wallet or Telegram, or come back tomorrow."
            ),
        )

    language = (body.language or "zh-TW").strip()
    if language not in _LANGUAGE_NAMES:
        language = "zh-TW"
    history = [m.model_dump() for m in body.history]

    try:
        text, used_model = await _answer(body.message, history, language, short_id)
    except TimeoutError:
        logger.warning("[Guest] LLM timeout guest=%s", short_id)
        raise HTTPException(status_code=504, detail="Analysis timed out, please retry.")
    except ValueError as exc:
        # 模型鏈全部因平台金鑰缺失而無法建立 client → 訪客模式優雅關閉
        logger.warning("[Guest] platform LLM key unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Guest mode is temporarily unavailable. "
                "Log in with an EVM wallet or Telegram to use your own API key."
            ),
        ) from exc
    except _EXPECTED_ERRORS as exc:
        logger.error("[Guest] LLM failed guest=%s: %s", short_id, exc)
        raise HTTPException(status_code=502, detail="Analysis failed, please retry.")

    remaining = _consume_quota(guest_id)
    logger.info(
        "[Guest] answered guest=%s model=%s remaining=%d/%d",
        short_id,
        used_model,
        remaining,
        _daily_limit(),
    )
    return GuestAnalyzeResponse(reply=text, remaining=remaining, limit=_daily_limit())


async def _answer(
    message: str, history: list[dict], language: str, short_id: str
) -> tuple[str, str]:
    """先跑 agent（最多 60 秒），不行就退回單次回答（用剩下的時間，整題 90 秒）。

    回傳 (回答, "agent:provider/model" 或 "basic:provider/model")。
    """
    deadline = asyncio.get_running_loop().time() + _TOTAL_BUDGET_SECONDS
    if guest_agent_enabled():
        try:
            return await asyncio.wait_for(
                _agent_answer(message, history, language),
                timeout=_AGENT_BUDGET_SECONDS,
            )
        except _EXPECTED_ERRORS as exc:
            # 只記例外類別：訊息可能夾帶 prompt／模型輸出
            logger.warning(
                "[Guest] agent path failed (%s), falling back to basic answer guest=%s",
                type(exc).__name__,
                short_id,
            )
    return await _basic_answer(message, history, language, short_id, deadline)


async def _agent_answer(
    message: str, history: list[dict], language: str
) -> tuple[str, str]:
    from core.agents.guest_agent import run_guest_agent  # noqa: PLC0415

    client, label = await run_sync(_agent_client)
    reply = await run_guest_agent(
        client, message, history, language, guest_rules=_GUEST_AGENT_RULES
    )
    return reply, f"agent:{label}"


async def _basic_answer(
    message: str,
    history: list[dict],
    language: str,
    short_id: str,
    deadline: float,
) -> tuple[str, str]:
    """單次呼叫、沒有工具（PR-4 之前的訪客回答），帶同樣的訪客規則與多輪歷史。"""
    from langchain_core.messages import (  # noqa: PLC0415
        AIMessage,
        HumanMessage,
        SystemMessage,
    )

    # 即時快照在配額扣用前抓（抓不到照樣可答，fail-open）
    snapshot = await _safe_snapshot()
    messages = [
        SystemMessage(
            content=_GUEST_SYSTEM_PROMPT.format(
                language=_LANGUAGE_NAMES[language], snapshot=snapshot
            )
        )
    ]
    for item in history:
        role_cls = HumanMessage if item["role"] == "user" else AIMessage
        messages.append(role_cls(content=item["content"]))
    messages.append(HumanMessage(content=message))

    result, used_model = await _invoke_with_model_fallback(messages, short_id, deadline)
    text = str(getattr(result, "content", "") or "").strip() or t(
        "errors.analysis.no_response", language
    )
    return text, f"basic:{used_model}"


async def _safe_snapshot() -> str:
    """同步行情抓取丟 thread pool（不在 event loop 做 sync I/O），3 秒逾時 fail-open。"""
    try:
        return await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _market_snapshot),
            timeout=3.0,
        )
    except _EXPECTED_ERRORS as exc:
        if not isinstance(exc, TimeoutError):
            logger.warning("[Guest] snapshot wrapper failed: %s", type(exc).__name__)
        return ""


def _healthy_candidates() -> list[tuple[str, str, Optional[str]]]:
    """模型候選（同步：本地模型要打健康探測，呼叫端丟 executor）。

    順序：免費會員同款平台模型（fallback_credentials，已過健康探測）→ 訪客鏈（GUEST_LLM_*）。
    回傳 (provider, model, api_key)；api_key=None 表示走 LLMClientFactory 讀平台 env key。
    """
    from core.agents.fallback import (  # noqa: PLC0415
        fallback_credentials,
        local_provider_healthy,
    )
    from core.model_config import get_default_model, is_local_provider  # noqa: PLC0415

    out: list[tuple[str, str, Optional[str]]] = []
    creds = fallback_credentials(tier="free")
    if creds:
        provider = creds["provider"]
        model = creds.get("model") or get_default_model(provider)
        out.append((provider, model, creds.get("api_key") or ""))
    seen = {(p, m) for p, m, _ in out}
    for provider, model in _model_chain():
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        if is_local_provider(provider) and not local_provider_healthy(provider):
            logger.warning("[Guest] local model %s is down, skipping", provider)
            continue
        out.append((provider, model, None))
    return out


def _build_client(
    provider: str, model: str, api_key: Optional[str], max_tokens: Optional[int] = None
):
    if api_key is None:
        return LLMClientFactory.create_client(provider=provider, model=model)
    return create_user_llm_client(
        provider=provider, api_key=api_key, model=model, max_tokens=max_tokens
    )


def _agent_client():
    """agent 路徑用第一個建得起來的候選模型（同步，丟 executor）。回傳 (client, "provider/model")。

    agent 失敗就整題退回單次回答（那邊才沿鏈換模型），這裡不重試。
    """
    last_exc: Exception | None = None
    for provider, model, api_key in _healthy_candidates():
        try:
            client = _build_client(provider, model, api_key, _AGENT_MAX_TOKENS)
        except ValueError as exc:
            last_exc = exc
            continue
        return client, f"{provider}/{model}"
    raise last_exc or ConnectionError("no guest model available")


async def _invoke_with_timeout(client, messages, timeout: float):
    loop = asyncio.get_running_loop()

    def _call():
        return client.invoke(messages, max_tokens=1200)

    return await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=timeout)


async def _invoke_with_model_fallback(messages, guest_id_short: str, deadline: float):
    """沿模型鏈重試：額度/可用性錯誤（429/404/...）換下一個模型；本地模型連不上也換。

    回傳 (result, "provider/model")。全部模型都不可用時拋出最後一個例外——
    呼叫端維持原本的 502 語意。平台金鑰完全缺失（ValueError）也逐模型
    嘗試後上拋，由呼叫端轉 503。每次嘗試只用到 deadline 為止（整題 90 秒）。
    """
    loop = asyncio.get_running_loop()
    candidates = await run_sync(_healthy_candidates)
    last_exc: Exception | None = None
    for provider, model, api_key in candidates:
        try:
            client = _build_client(provider, model, api_key)
        except ValueError as exc:
            last_exc = exc
            logger.warning("[Guest] client init failed model=%s: %s", model, exc)
            continue
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("guest answer budget exhausted")
        try:
            result = await _invoke_with_timeout(client, messages, remaining)
            return result, f"{provider}/{model}"
        except _EXPECTED_ERRORS as exc:
            last_exc = exc
            if not _is_switchable_error(exc, provider):
                raise
            logger.warning(
                "[Guest] model %s/%s quota/unavailable, trying next (guest=%s): %s",
                provider,
                model,
                guest_id_short,
                exc,
            )
    raise last_exc or ConnectionError("no healthy guest model")
