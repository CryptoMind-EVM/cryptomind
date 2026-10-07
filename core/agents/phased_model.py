"""分階段模型綁定 — 規劃回合不推理，最終回答才動用推理預算。

## 為什麼需要這個

2026-09-06 實測（deepseek-v4-flash，一題「推薦可能會漲的加密貨幣」36 秒）：

| 回合 | 耗時 | 輸出 token | 其中推理 | 可見文字 | 工具 |
|---|---|---|---|---|---|
| 1 | 3.3s | 381 | 256 (67%) | 0 ch | 5 個（args 合計 8 字元）|
| 2 | 10.0s | 1163 | 906 (78%) | 67 ch | 6 個（args 合計 105 字元）|
| 3 | 19.4s | 2072 | 1086 (52%) | 1453 ch | 無（最終回答）|

吐字速度固定在 110~127 tok/s，變異極小，所以 **延遲 ≈ 輸出 token ÷ 115**。
輸入端幾乎免費（20K token 的 system prompt，第 2 回合起快取命中 88%、97%）。
前兩回合燒掉 1162 個推理 token（≈10 秒）只為了選出 6 個 symbol——那是純粹
可以省掉的部分，最終回答的推理才是使用者真正買單的東西。

## 為什麼不是「跑完發現沒叫工具就換模型重跑」

那個直覺解法實測不成立，兩個原因：

1. **會漏字**：middleware 內部呼叫 ``handler()`` 產生的 token 一樣會進
   ``astream`` 的 messages 串流。實測一個呼叫兩次 handler 的 middleware，
   前端收到的是兩次的文字接在一起（`'你好嗎我很好'`）——被丟棄的那次已經
   送到使用者眼前了。
2. **不划算**：被丟棄的那次如果是在寫答案，就是 ~800 個 token ≈ 7 秒的純浪費。
   短題目（1 輪工具 + 回答）只省得到第 1 回合的 256 個推理 token，淨值是負的。

所以改用 **sentinel tool**：工具清單裡多一個 ``compose_final_answer``，模型
覺得資料夠了就叫它，middleware 攔下來換回答模型重跑。被丟棄的那回合成本是
一個 ~23 token 的工具呼叫（實測，≈0.3 秒），不是 800 token 的散文。

## 為什麼不能用 tool_choice="any" 逼它只准叫工具

那是第一版的做法，實測直接爆炸：強制每回合都要叫工具就等於拿掉模型「停下來」
的能力，加上關掉推理後它連「這個資料我剛才抓過了」都判斷不出來，於是連叫 20
回合重複工具（``get_crypto_price`` 出現 6 次、4 次、4 次…），撞穿 recursion_limit
之後 claw_loop 又整個重跑，**72.3 秒**——比不開這功能還慢一倍。

同一組對照也證明了問題不在關推理：全程關推理但**不**強制 tool_choice，
同一題 4 個回合 18.8 秒收斂（基準 36.6 秒），答案 1358 字元完整。
所以規劃回合只改推理預算，工具要不要叫留給模型自己決定。

## 模型不叫 sentinel、直接寫散文的時候

就採用它寫的答案，不丟棄也不重跑。丟棄要付兩次代價：被丟掉的 token 已經
串到使用者眼前了（實測 middleware 內部呼叫 handler 產生的 token 一樣會進
astream），而且那是 ~900 token ≈ 8 秒的純浪費。拿到的是「快設定寫的答案」
而不是「強設定寫的答案」，但沒有浪費也沒有漏字。

## Provider 限制：DeepSeek 不能在同一段對話裡混用推理／不推理

實測（2026-09-06，直接打 API 確認）：規劃回合用 ``reasoning_effort=none`` 產生的
assistant 訊息沒有 ``reasoning_content``，之後那個開推理的回答回合就會被擋：

    400 The `reasoning_content` in the thinking mode must be passed back to the API.

補一個 ``reasoning_content=""`` 回去 API 是收的（實測 ✓），但 langchain-openai 的
``_convert_message_to_dict`` 只搬 content/role/tool_calls，``additional_kwargs``
裡的 ``reasoning_content`` 會被丟掉——沒有 monkeypatch 就送不出去。

所以在 DeepSeek 上這個功能無法生效。middleware 偵測到這個 400 就**降級成
全程沿用規劃設定**（等於全域關推理），並記一筆 WARNING，而不是讓每一題都
400 → 重跑 → 掉到殘缺的 fallback 答案（實測那條路徑要 36.5 秒還答得更差）。

OpenAI / Groq 這類沒有這個限制的 provider 不受影響。

## Provider 跳過清單（``PHASED_MODEL_SKIP_PROVIDERS``，預設 ``deepseek``）

上面那個降級在 DeepSeek 上的實際結果是：整題不推理、前端可摺疊的思考區塊
整題空白、而且每題多付一次被拒的 400。延遲省幾個百分點，換掉的是使用者看得
到的思考過程——對主力使用 DeepSeek 免費層的族群不划算。所以旗標開了也
**預設跳過 DeepSeek**（從 client 的 base_url／class 推 provider），其他 provider
照常吃到加速。想在 DeepSeek 上硬開就把這個變數設成空字串。

## 快取注意事項

回答回合刻意**不把 sentinel 從工具清單拿掉**，只設 ``tool_choice="none"``。
工具定義是 provider 端 prompt 前綴的一部分，中途改清單會讓 88~97% 的快取
命中整個失效，省下來的遠不如賠掉的。

## 回答回合模型還是想叫工具（2026-10-03 事故）

``tool_choice="none"`` 只是「不准叫」，不是「提示裡沒有工具」。Qwen3.5/3.6 系的
agent 模型（實測：Occamy-1.0）在這個狀態下仍想叫工具，會把呼叫格式直接寫成
**文字**：``<tool_call><function=web_search><parameter=query>…``。因為 ``tool_choice``
是 none，伺服器不會把它解析成工具呼叫；下游的 sanitizer 又只剝標籤、留內容，
使用者最後看到的「分析」就是那次工具呼叫的參數——問「SMR 值不值得買」，答案是
一行搜尋關鍵字。起因通常是 ToolGuard 剛攔下一次相似搜尋，或規劃回合撞到上限
（模型本來就還想查）。

重現（本機 Occamy-1.0 IQ4_XS，8 次）：現況 0/8 寫出正文；NeoHorse／DeepSeek 不會。
所以：

1. 第一次回答仍走原本的快取路徑，但加 ``stop=["<tool_call>"]``：模型一開始要寫
   工具呼叫就當場收手，得到**空回覆**（可偵測、不會有垃圾字串串給使用者）。
2. 空回覆就**重試一次**：拿掉 sentinel（模型想叫的就是它，保留時 2/8）、其他工具
   原封不動（有些 provider 要求歷史裡有工具呼叫就必須有工具定義）、結尾加一句
   「資料足夠，直接寫最終分析」。實測 8/8 寫出正文。重試只在第一次空回覆時才發生，
   行為正常的模型零額外成本。
3. 重試還是空 → 照原樣往上交，由既有的空回覆兜底訊息處理，不再重試第三次。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

FINALIZE_TOOL_NAME = "compose_final_answer"

# 回答回合的停止詞：Qwen 系模型想叫工具時一定以它開頭（見模組說明「回答回合模型還是想叫工具」）。
_TOOL_MARKUP_STOP = "<tool_call>"

# 重試時附在對話最後的一句話。刻意用英文＋「跟使用者同一種語言」：平台有多語系，
# 寫死繁中會讓英文使用者收到中文答案（實測中、英提問都 8/8 正常寫出正文）。
_CLOSING_NUDGE = (
    "(System note) You already have enough data. Write the complete final analysis "
    "now, in the same language as the user's question. Do not call any tools and do "
    "not output tool-call markup."
)


@tool(FINALIZE_TOOL_NAME)
def finalize_sentinel() -> str:
    """需要的資料都齊了、準備寫最終回答時呼叫這個。呼叫它就不要再叫其他工具。"""
    # 永遠不會真的執行：middleware 攔截到這個呼叫就丟棄整個回合，
    # 改用回答模型重跑，graph 根本看不到它。
    return ""


def phased_model_enabled() -> bool:
    """分階段模型綁定是否啟用。預設關——行為等價於原本的單模型 ReAct。"""
    from core.feature_flags import env_flag

    return env_flag("PHASED_MODEL_ENABLED")


_DEFAULT_SKIP_PROVIDERS = "deepseek"


def phased_skip_providers() -> frozenset:
    """旗標開了也不掛 middleware 的 provider（小寫、逗號分隔）。

    未設＝``deepseek``（見模組說明）；設成空字串＝不跳過任何 provider。
    """
    import os

    raw = os.getenv("PHASED_MODEL_SKIP_PROVIDERS")
    if raw is None:
        raw = _DEFAULT_SKIP_PROVIDERS
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def provider_of_llm(llm: Any) -> str:
    """從 LangChain client 推 PROVIDER_REGISTRY 的 provider 名；推不出回空字串。

    BYOK client 建好之後只剩 base_url／class 可辨識（create_user_llm_client
    不會把 provider 名留在物件上）：OpenAI 相容端點看 base_url 的 host 對
    registry；官方 SDK（無 base_url）看 class 名。LanguageAwareLLM 包一層
    要看得穿（``_llm``）。
    """
    from urllib.parse import urlparse

    inner = getattr(llm, "_llm", llm)
    base = getattr(inner, "openai_api_base", None) or getattr(inner, "base_url", None)
    if base:
        from core.model_config import PROVIDER_REGISTRY

        host = urlparse(str(base)).netloc.lower()
        for name, runtime in PROVIDER_REGISTRY.items():
            rt_base = runtime.get("base_url")
            if rt_base and urlparse(rt_base).netloc.lower() == host:
                return name
        return ""
    cls = type(inner).__name__.lower()
    if "anthropic" in cls:
        return "anthropic"
    if "google" in cls or "gemini" in cls:
        return "google_gemini"
    if "openai" in cls:
        return "openai"
    return ""


def _planner_settings() -> dict:
    """規劃回合要疊上去的 model_settings。

    ``PHASED_PLANNER_REASONING_EFFORT`` 預設 ``none``（DeepSeek 實測可完全消掉
    reasoning token；OpenAI o 系列吃 low/medium/high，設錯值會 400，所以留成
    可設定的而不是寫死）。設成空字串就不送這個參數。
    """
    import os

    effort = os.getenv("PHASED_PLANNER_REASONING_EFFORT", "none").strip()
    return {"reasoning_effort": effort} if effort else {}


def _planner_model_name() -> str:
    """規劃回合要換用的模型名。空的話沿用同一個模型，只改 settings。

    BYOK 使用者常常只綁一把金鑰一個模型，換模型會打到不存在的 endpoint，
    所以「同模型關推理」必須是預設可用的路徑。
    """
    import os

    return os.getenv("PHASED_PLANNER_MODEL", "").strip()


def _split_finalize(response: Any) -> tuple[bool, Any]:
    """判斷回應是不是在宣告「要寫答案了」。

    回傳 ``(是否進入回答階段, 可能已剝掉 sentinel 的 response)``：

    - 只叫了 sentinel → ``(True, response)``，呼叫端丟棄它、換回答模型重跑
    - sentinel 混著真工具 → ``(False, 剝掉 sentinel 的 response)``，真工具照跑
    - 沒叫 sentinel → ``(False, response)``
    """
    msgs = getattr(response, "result", None)
    if not msgs:
        return False, response
    last = msgs[-1]
    if not isinstance(last, AIMessage):
        return False, response
    calls = list(getattr(last, "tool_calls", None) or [])
    if not any(c.get("name") == FINALIZE_TOOL_NAME for c in calls):
        return False, response

    rest = [c for c in calls if c.get("name") != FINALIZE_TOOL_NAME]
    if not rest:
        return True, response

    # sentinel 跟真工具同一回合：把 sentinel 剝掉，讓真工具跑完再說。
    stripped = last.model_copy(update={"tool_calls": rest})
    return False, response.__class__(
        result=[*msgs[:-1], stripped],
        structured_response=getattr(response, "structured_response", None),
    )


def _is_phase_mix_rejected(exc: Exception) -> bool:
    """這個錯誤是不是「provider 不准同一段對話混用推理／不推理」。

    只認這一種才降級——其他錯誤（429、供應商 5xx）要往上拋，讓既有的 retry
    層處理，不能被這裡吞掉變成安靜的品質下降。
    """
    return "reasoning_content" in str(exc)


def _content_text(content: Any) -> str:
    """AIMessage.content 可能是字串，也可能是 content block 清單；只取可見文字。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b if isinstance(b, str) else str(b.get("text") or "")
            for b in content
            if isinstance(b, (str, dict))
        )
    return ""


def _is_blank_answer(response: Any) -> bool:
    """回答回合拿到「沒有工具呼叫、也沒有任何可見文字」的回覆。

    加了 ``stop=["<tool_call>"]`` 之後，模型一想把工具呼叫寫成文字就會在第一個
    token 收手，剩下的就是這種空回覆——它是「模型還想叫工具」的訊號。
    """
    msgs = getattr(response, "result", None)
    if not msgs:
        return False
    last = msgs[-1]
    if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
        return False
    return not _content_text(last.content).strip()


def _with_tool_markup_stop(settings: Optional[dict]) -> dict:
    """在 model_settings 加上 ``<tool_call>`` 停止詞，保留呼叫端原本設的 stop。"""
    settings = dict(settings or {})
    stop = settings.get("stop")
    stops = [stop] if isinstance(stop, str) else list(stop or [])
    if _TOOL_MARKUP_STOP not in stops:
        stops.append(_TOOL_MARKUP_STOP)
    settings["stop"] = stops
    return settings


class PhasedModelMiddleware(AgentMiddleware):
    """規劃回合走便宜設定，最終回答回合走原本的模型設定。

    ``planner_model`` 為 None 時只改 ``model_settings``（同一個模型關推理），
    這是 BYOK 單金鑰使用者唯一能用的模式。

    sentinel 走 ``middleware.tools`` 註冊，不是每回合用 ``request.override(tools=...)``
    塞進去——後者 langchain 會擋（工具沒註冊就無法執行），而且每回合改工具清單
    會動到 provider 端的 prompt 前綴，把 88~97% 的快取命中打掉。
    """

    tools = [finalize_sentinel]

    def __init__(
        self,
        planner_settings: Optional[dict] = None,
        planner_model: Any = None,
        max_planner_turns: int = 8,
    ) -> None:
        super().__init__()
        # provider 拒絕混用推理／不推理時翻起來，之後整段對話都沿用規劃設定
        self._degraded = False
        self._planner_settings = planner_settings or {}
        self._planner_model = planner_model
        # 模型一直不叫 sentinel 時的保險絲：超過就直接進回答階段，
        # 不要讓「便宜設定」把整題拖在規劃階段（第一版就是這樣爆的）。
        self._max_planner_turns = max_planner_turns

    # ── request 組裝 ────────────────────────────────────────────────────
    def _planner_turns_used(self, request: Any) -> int:
        """已經跑過幾個 AI 回合——用來擋住規劃階段無限延伸。"""
        msgs = (getattr(request, "state", None) or {}).get("messages") or []
        return sum(1 for m in msgs if isinstance(m, AIMessage))

    def _plan_request(self, request: Any) -> Any:
        overrides: dict = {}
        if self._planner_model is not None:
            overrides["model"] = self._planner_model
        if self._planner_settings:
            overrides["model_settings"] = {
                **(request.model_settings or {}),
                **self._planner_settings,
            }
        return request.override(**overrides)

    def _answer_request(self, request: Any) -> Any:
        # 工具清單原封不動（含 sentinel），只是不准再叫——見類別 docstring。
        # stop：模型還想叫工具時當場收手，不要把工具呼叫寫成文字串給使用者。
        return request.override(
            tool_choice="none",
            model_settings=_with_tool_markup_stop(
                getattr(request, "model_settings", None)
            ),
        )

    def _retry_answer_request(self, request: Any) -> Any:
        """第一次回答是空回覆（模型還想叫工具）時的重試請求。

        只拿掉 sentinel——模型一直想叫的就是它（保留時 8 次只有 2 次寫出正文）；
        其他工具原封不動，有些 provider 要求歷史裡有工具呼叫就必須有工具定義。
        結尾補一句「資料足夠，直接寫」。
        """
        tools = [
            t
            for t in (getattr(request, "tools", None) or [])
            if getattr(t, "name", None) != FINALIZE_TOOL_NAME
        ]
        messages = [
            *(getattr(request, "messages", None) or []),
            HumanMessage(content=_CLOSING_NUDGE),
        ]
        return request.override(
            tools=tools,
            messages=messages,
            tool_choice="none",
            model_settings=_with_tool_markup_stop(
                getattr(request, "model_settings", None)
            ),
        )

    async def _aanswer(self, request: Any, handler: Any) -> Any:
        """回答回合：先走快取路徑；空回覆就重試一次（最多一次）。"""
        response = await handler(self._answer_request(request))
        if not _is_blank_answer(response):
            return response
        logger.warning(
            "[PhasedModel] 回答回合只想叫工具、沒有正文，拿掉 sentinel 並提示直接寫答案後重試一次"
        )
        return await handler(self._retry_answer_request(request))

    def _answer(self, request: Any, handler: Any) -> Any:
        response = handler(self._answer_request(request))
        if not _is_blank_answer(response):
            return response
        logger.warning(
            "[PhasedModel] 回答回合只想叫工具、沒有正文，拿掉 sentinel 並提示直接寫答案後重試一次"
        )
        return handler(self._retry_answer_request(request))

    def _degraded_answer_request(self, request: Any) -> Any:
        """降級後的收尾：禁止再叫工具，但保留規劃設定（開回推理就會再撞 400）。

        少了這個，模型會在降級後把 sentinel 再叫一次才肯寫答案，白花一次呼叫。
        """
        return self._plan_request(request).override(tool_choice="none")

    def _degrade(self, exc: Exception) -> None:
        logger.warning(
            "[PhasedModel] provider 拒絕混用推理／不推理，本次對話降級為全程"
            "沿用規劃設定（等同全域關推理）：%s",
            exc,
        )
        self._degraded = True

    # ── hooks ───────────────────────────────────────────────────────────
    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._degraded:
            response = await handler(self._plan_request(request))
            done, response = _split_finalize(response)
            if not done:
                return response
            return await handler(self._degraded_answer_request(request))
        if self._planner_turns_used(request) >= self._max_planner_turns:
            logger.info("[PhasedModel] 規劃回合達上限，直接進回答階段")
            return await self._aanswer(request, handler)
        response = await handler(self._plan_request(request))
        done, response = _split_finalize(response)
        if not done:
            return response
        logger.info("[PhasedModel] 進入回答階段，換回原模型")
        try:
            return await self._aanswer(request, handler)
        except Exception as exc:
            if not _is_phase_mix_rejected(exc):
                raise
            self._degrade(exc)
            return await handler(self._degraded_answer_request(request))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._degraded:
            response = handler(self._plan_request(request))
            done, response = _split_finalize(response)
            if not done:
                return response
            return handler(self._degraded_answer_request(request))
        if self._planner_turns_used(request) >= self._max_planner_turns:
            logger.info("[PhasedModel] 規劃回合達上限，直接進回答階段")
            return self._answer(request, handler)
        response = handler(self._plan_request(request))
        done, response = _split_finalize(response)
        if not done:
            return response
        logger.info("[PhasedModel] 進入回答階段，換回原模型")
        try:
            return self._answer(request, handler)
        except Exception as exc:
            if not _is_phase_mix_rejected(exc):
                raise
            self._degrade(exc)
            return handler(self._degraded_answer_request(request))
