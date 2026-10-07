"""Rate-limit error classification — shared across agent layers.

Hermes-style：只有 rate-limit 錯誤值得 retry，其他錯誤（401/403/402/degraded）
fail-fast 走友善訊息，避免把免費方案額度浪費在必然失敗的重試上。

過去這套邏輯只活在 ``manager/claw_loop.py``，但 ``base_react_agent.execute_streaming``
在內部把 LLM 例外直接轉成 ``AgentResult(success=False)``，導致 claw_loop 的
retry 路徑永遠不會被觸發（NVIDIA NIM 的 ``ResourceExhausted`` 因此原文噴進
聊天泡泡）。把分類邏輯抽到這裡讓兩層共用，**避開循環依賴**——這個模組只依賴
typing，不引用任何 agent 內部類別，因此 ``base_react_agent.py`` 與
``manager/claw_loop.py`` 都可以安全 import。

放在 ``core/agents/`` 根目錄而非 ``manager/`` 子層：避免 ``base_react_agent``
import 時觸發 ``core.agents.manager.__init__`` 載入鏈（manager 又 import
base_react_agent 會循環）。

認列的 marker（substring match，case-insensitive）：

- ``429`` / ``too many requests`` / ``rate limit`` — OpenAI / Anthropic / 通用 HTTP
- ``resourceexhausted`` — gRPC 標準 429 code，NVIDIA NIM endpoint 使用
- ``worker local total request limit`` — NIM 內部訊息（``16/16`` 那種）
"""

from __future__ import annotations

#: 可重試的速率限制 marker 清單。
RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "429",
    "too many requests",
    "rate limit",
    "resourceexhausted",  # NVIDIA NIM / gRPC 429
    "worker local total request limit",  # NIM 內部訊息
)

#: 配額/容量耗盡 marker（學 Hermes fallback-providers doc 的 capacity phrase 表）。
#:
#: 與 transient rate limit（429，等一下就能成功）不同：capacity error 是
#: 「每日配額耗盡 / 餘額不足 / 付款失敗」——retry 無意義，該換 provider 或
#: 提示使用者充值。各家 provider 文案不一致，這裡收常見的。
#:
#: 來源：Hermes fallback-providers doc 認列的 capacity-equivalent phrases：
#:   - Vertex/GCP: ``RESOURCE_EXHAUSTED`` / ``quota exceeded``
#:   - Bedrock/LiteLLM: ``Too many tokens per day`` / ``daily limit``
#:   - 通用: ``daily quota`` / ``quota_exceeded``
#:   - 加上 OpenAI 的 ``exceeded your current quota`` 與 402 系列
CAPACITY_MARKERS: tuple[str, ...] = (
    "402",
    "insufficient balance",
    "insufficient credits",
    "insufficient_quota",
    "exceeded your current quota",
    "quota exceeded",
    "quota_exceeded",
    "resource_exhausted",  # Vertex/GCP（注意：NIM 的 resourceexhausted 是並發，歸 rate limit）
    "daily limit",
    "daily quota",
    "too many tokens per day",
    "payment required",
    "billing",
)


def is_rate_limit_error(error: BaseException) -> bool:
    """是否為**可重試的暫時**速率限制錯誤（transient rate limit）。

    注意：配額/容量耗盡（daily quota / 402 / 餘額不足）**不是** transient rate
    limit——retry 無意義。用 :func:`is_capacity_error` 區分。本函式只認暫時性
    的 429 / NIM 並發上限（等 slot 釋放就能成功）。

    Args:
        error: 任何 exception 實例（會取 ``str(error).lower()`` 做 substring match）。

    Returns:
        True 若錯誤訊息含任一 ``RATE_LIMIT_MARKERS`` 且**不是** capacity error。
    """
    msg = str(error).lower()
    # capacity error 優先判斷：402 配額耗盡可能也帶 429 字眼，但不能 retry
    if any(marker in msg for marker in CAPACITY_MARKERS):
        return False
    return any(marker in msg for marker in RATE_LIMIT_MARKERS)


def is_capacity_error(error: BaseException) -> bool:
    """是否為配額/容量耗盡錯誤（capacity error）。

    與 :func:`is_rate_limit_error` 區分：
    - transient rate limit（429）：等一下就能成功，retry 有意義
    - capacity error（quota/402/餘額）：retry 無意義，該換 provider 或提示充值

    用於：決定是否 retry（rate limit = retry，capacity = fail-fast/換 provider）。

    Args:
        error: 任何 exception 實例。

    Returns:
        True 若錯誤訊息含任一 ``CAPACITY_MARKERS``。
    """
    msg = str(error).lower()
    return any(marker in msg for marker in CAPACITY_MARKERS)


#: 供應商 5xx 伺服器錯誤 marker（transient — 過載/內部錯誤，retry 通常能救回）。
#:
#: 與 rate limit（429）同類：都是供應商「暫時」忙不過來，等一下就能成功；
#: 與 capacity（402/quota）不同：5xx 不代表額度用罄，retry 有意義。
#:
#: 用描述性字串而非裸數字（``"500"``）——後者太容易誤判（如
#: ``"retrieved 500 results"``、token count）。這些 type/message 字串只會
#: 出現在真正的供應商錯誤裡。
#:
#: 線上案例：``Error code: 500 - {'error': {'message': 'Internal server error.',
#: 'type': 'InternalServerError', 'code': 500}}`` —— 含 "internal server error"
#: 與 "internalservererror" 兩個 marker。
SERVER_ERROR_MARKERS: tuple[str, ...] = (
    "internalservererror",  # OpenAI/Anthropic 例外 type 欄位
    "internal server error",  # 例外 message 欄位
    "serviceunavailable",  # 503 type 欄位
    "service unavailable",  # 503 message 欄位
    "bad gateway",  # 502
    "server overload",
)


def is_transient_error(error: BaseException) -> bool:
    """是否為**可重試的暫時性**錯誤（transient rate limit OR 供應商 5xx）。

    統一 retry 決策點：rate limit（429/NIM 並發）與供應商 5xx（500/503 過載）
    都是「等一下就能成功」的暫時性錯誤，retry 一次合理。capacity error
    （402/quota/餘額不足）不在內——retry 必然失敗，只浪費免費方案額度。

    Args:
        error: 任何 exception 實例。

    Returns:
        True 若為 rate limit 或供應商 5xx（且不是 capacity error）。
    """
    if is_capacity_error(error):
        return False  # 配額耗盡不可 retry
    if is_rate_limit_error(error):
        return True
    msg = str(error).lower()
    return any(marker in msg for marker in SERVER_ERROR_MARKERS)


#: NIM worker 並發上限 marker（``Worker local total request limit reached (N/32)``）。
#:
#: 與一般 429 的差別：429 是「配額速率限制」（通常等幾秒即可），worker local
#: limit 是「這台 worker 的 in-flight slot 滿了」——slot 要等執行中的推論結束才
#: 釋放（大模型推論常 5-30 秒）。2 秒短 backoff 幾乎必然落在同一擁擠 window，
#: retry 必然再 exhausted。這類錯誤需要更長的 backoff（對齊友善訊息承諾的 ~30 秒）。
WORKER_OVERLOAD_MARKERS: tuple[str, ...] = (
    "worker local total request limit",
)


def is_worker_overload_error(error: BaseException) -> bool:
    """是否為 NIM worker 並發上限錯誤（worker local total request limit）。

    這類錯誤仍屬 transient（等 slot 釋放可成功，見 :func:`is_transient_error`），
    但需要**顯著更長的 backoff**——worker slot 釋放時間遠長於一般 429。
    用於呼叫端選擇退避策略：worker overload 用長 backoff，其他 transient 用短 backoff。

    Args:
        error: 任何 exception 實例。

    Returns:
        True 若錯誤訊息含 worker local limit marker。
    """
    msg = str(error).lower()
    return any(marker in msg for marker in WORKER_OVERLOAD_MARKERS)
