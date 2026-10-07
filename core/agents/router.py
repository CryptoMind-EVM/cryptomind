"""Router — 一次性 supervisor 的分流決策（Model Mixer Step 3）.

設計：docs/plans/2026-09-03-model-mixer-step3-router.md（DANNY 授權自行決策）
上游：docs/plans/2026-09-02-model-mixer-design.md §3（兩軸分流）

決策架構（企業級原則）：

1. **Router 只輸出「任務性質」，不輸出模型選擇**（§3）——
   ``{"domains": [...], "depth": "lookup|analysis|research", "gate": ...}``。
   depth=lookup 且 domains=[] 才走 fast path；有 domain 或更深任務走 ReAct。
   gate 在 Step 5 前只記錄（RunMetrics／eval）。
2. **確定性層在 LLM 之前**（D1=B）：T0 白名單是「Router 決策的確定性子集」
   （命中＝零成本 lookup），金融訊號是「硬否決」（零成本 research）。
   兩者都命中不了才付一次 Router LLM 呼叫——真正的市場問題延遲不變。
3. **fail-closed**：空 query／逾時／例外／解析失敗／schema 不合法 →
   一律 ``depth=research``（＝現況 worst case，寧可慢不可錯）。
4. **注入威脅模型**：Router 輸出只進分支邏輯與指標，永不進下游 prompt；
   惡意 query 最壞影響＝走錯分支，仍被 fast-path 數字丟棄與硬否決兜住。

模型級距（D2）：中階 mini 起步（``task_type="router"`` → ModelRouter 的
``gpt-5.4-mini``；BYOK 使用者沿用自家模型），eval 量測後再調。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Optional, TypedDict

from .triage import has_finance_signal, is_smalltalk

DEPTH_LOOKUP = "lookup"
DEPTH_ANALYSIS = "analysis"
DEPTH_RESEARCH = "research"

_VALID_DEPTHS = (DEPTH_LOOKUP, DEPTH_ANALYSIS, DEPTH_RESEARCH)
_GATE_RISK_FIRST = "risk_first"

# Router 呼叫的延遲預算。逾時 = fail-closed 走 research（現況 worst case），
# 不讓分流決策本身成為新的延遲來源。
#
# **可設定**（``ROUTER_TIMEOUT_SECONDS`` env）：4 秒是「中階 mini」級距的
# 預設，不是對可用模型的限制。BYOK 使用者若要用 reasoning 模型當 router
# （思考 token 讓首字延遲以十秒計），把預算調大即可——寫死 4 秒等於替
# 使用者決定他只能用哪一種模型，而逾時的表現是「每題都 fail-closed 成
# research，還照付一次呼叫」，從外面完全看不出原因。
def _router_timeout_default() -> float:
    raw = os.getenv("ROUTER_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else 4.0
    except ValueError:
        return 4.0
    # 下限防呆：0 或負值會讓 wait_for 立刻逾時＝router 永遠不生效
    return value if value > 0 else 4.0


ROUTER_TIMEOUT_SECONDS = _router_timeout_default()

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_ROUTER_PROMPT = """You are the router of a financial analysis assistant. \
Classify the user message by task nature, not by difficulty.

Return ONLY one JSON object, nothing else:
{{"domains": [...], "depth": "lookup"|"analysis"|"research", "gate": "risk_first"|null}}

domains — zero or more of: {domains} (use [] if none apply)
depth —
  lookup: answerable with a single fact / data lookup (a price, a balance, a rate, \
a definition, small talk, a question about the assistant itself). No comparison, \
no judgment, no prediction.
  analysis: needs multiple data points, comparison, or a reasoned assessment \
(trends, which-is-better, should-I-buy).
  research: open-ended, multi-step investigation, forecasting, or industry/deep dives.
gate — "risk_first" only if the message involves moving money, transfers, or asks \
whether an address / token / counterparty is safe; otherwise null.

Rules:
- Any message that needs external data (prices, balances, news, project or user \
info) MUST include the matching domain(s). Small talk and questions about the \
assistant itself have no domains ([]).
- When unsure, choose the deeper depth. A question that asks for advice or a \
prediction is never lookup.

User message: {query}"""


def valid_domains() -> tuple:
    """合法 domain 詞彙表＝ProfileCatalog 的 agent ids（§2：新增 agent 時
    router 不用改——catalog 加檔即自動出現在這裡與 prompt 裡）。"""
    from .profile_catalog import get_profile_catalog

    return tuple(sorted(p.id for p in get_profile_catalog().list_profiles()))


class RouteMetrics(TypedDict):
    """``log_run_metrics`` 行尾欄位的契約。

    這裡加欄位 = ``log_run_metrics`` 必須同步加參數，否則 ``**metrics_dict()``
    展開時參數綁定失敗。那個 TypeError 發生在函式**進入之前**，呼叫端的
    try/except 來不及吞——2026-09-05 每次 Mixer 收尾都炸的線上 P0（#644→#650）。
    """

    depth: str
    domains: str | None
    gate: str | None
    source: str
    reason: str | None


@dataclass(frozen=True)
class RouteDecision:
    """Router 決策。

    ``is_fast_path``＝depth 為 lookup **且無 domain**——fast path 不掛工具、
    丟棄含數字回覆，只有純寒暄／自身問題（單一事實但零外部資料）能安全
    走它；lookup＋有 domain（如「台積電本益比」）是單點**資料**查詢，
    必須走 ReAct 用工具拿（§3：領域軸決定工具）。
    """

    domains: tuple
    depth: str
    gate: Optional[str]
    source: str  # "t0_cache" / "veto" / "router" / "fallback"
    # fallback 的原因。source="fallback" 時，逾時與 API 錯誤在 log 上長得
    # 一模一樣——但「調大 ROUTER_TIMEOUT_SECONDS」只對前者有用，對 429／
    # 認證失敗完全沒用。沒有這欄就只能瞎調。
    # （#629 在 eval harness 修過同一個問題，production 這條路沒修到。）
    reason: Optional[str] = None

    @property
    def is_fast_path(self) -> bool:
        return self.depth == DEPTH_LOOKUP and not self.domains

    def metrics_dict(self) -> RouteMetrics:
        """RunMetrics 行尾欄位（domains 以逗號接續，缺漏由呼叫端補 -）。
        source 區分 t0_cache（零成本快取）與 router（真 LLM 路由）——
        eval 從生產 log 對帳成本與詞表一致性時要靠它。

        回傳型別刻意是 ``RouteMetrics`` 而不是 ``dict``：呼叫端用
        ``**metrics_dict()`` 展開，回 ``dict`` 的話 mypy 完全看不見鍵名，
        這裡加一個欄位而 ``log_run_metrics`` 沒跟上就會 runtime TypeError
        ——#644 就是這樣變成線上 P0 的（見 test_metrics_dict_typing.py）。"""
        return {
            "depth": self.depth,
            "domains": ",".join(self.domains) if self.domains else None,
            "gate": self.gate,
            "source": self.source,
            "reason": self.reason,
        }


def _fallback(reason: str) -> RouteDecision:
    """fail-closed 成 research。``reason`` 必填——省略它就回到「所有降級長得
    一樣」的狀態，那正是這個欄位存在的理由。"""
    return RouteDecision(
        domains=(), depth=DEPTH_RESEARCH, gate=None, source="fallback",
        reason=reason,
    )


def _parse_router_reply(raw: str) -> RouteDecision:
    """解析模型回覆 → RouteDecision；任何不合法一律 fail-closed research。"""
    text = str(raw or "").strip()
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return _fallback("no_json")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return _fallback("bad_json")
    if not isinstance(payload, dict):
        return _fallback("not_object")

    depth = str(payload.get("depth") or "").strip().lower()
    if depth not in _VALID_DEPTHS:
        return _fallback("bad_depth")

    valid = set(valid_domains())
    raw_domains = payload.get("domains")
    # Empty is meaningful: only an explicit, valid [] permits no-tool lookup.
    # Coercing malformed/unknown domains to [] turns data queries into small talk.
    if not isinstance(raw_domains, list) or any(
        not isinstance(domain, str) or domain not in valid for domain in raw_domains
    ):
        return _fallback("bad_domains")
    domains = set(raw_domains)

    gate_raw = payload.get("gate")
    gate = _GATE_RISK_FIRST if gate_raw == _GATE_RISK_FIRST else None

    return RouteDecision(
        domains=tuple(sorted(domains)), depth=depth, gate=gate, source="router"
    )


async def route_query(
    query: str,
    llm_invoke,
    *,
    timeout_s: float = ROUTER_TIMEOUT_SECONDS,
) -> RouteDecision:
    """分流決策入口（claw_loop 唯一呼叫點）。

    分層：T0 白名單（零成本）→ 金融訊號硬否決（零成本）→ Router LLM
    （帶逾時）。``llm_invoke`` 是 ``async (prompt) -> str``，由呼叫端注入
    ``task_type="router"`` 的模型選擇（與 triage.classify_query 同模式）。
    """
    if not query or not query.strip():
        return _fallback("empty_query")

    # 1) 確定性快取：白名單命中的寒暄＝Router 也會判 lookup 的子集（D1=B）
    if is_smalltalk(query):
        return RouteDecision(
            domains=(), depth=DEPTH_LOOKUP, gate=None, source="t0_cache"
        )

    # 2) 硬否決：有金融訊號 → 不可能是 lookup，連 Router 都不跑（省成本＋防誤判）
    if has_finance_signal(query):
        return RouteDecision(
            domains=(), depth=DEPTH_RESEARCH, gate=None, source="veto"
        )

    prompt = _ROUTER_PROMPT.format(
        domains=", ".join(valid_domains()), query=query
    )
    try:
        raw = await asyncio.wait_for(llm_invoke(prompt), timeout=timeout_s)
    except asyncio.TimeoutError:
        return _fallback("timeout")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # 帶上例外型別：429、認證失敗、連線中斷在 log 上要分得出來，
        # 否則「調大 timeout」跟「換金鑰」之間仍然只能猜。
        return _fallback(f"invoke_error:{type(exc).__name__}")
    return _parse_router_reply(raw)
