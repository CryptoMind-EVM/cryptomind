"""
CryptoMindAgent — the single CLAW-style agent with ALL tools.

Replaces the 9 specialist agents (CryptoAgent, TWStockAgent, etc.)
with one agent that has access to every tool and a combined system
prompt covering all markets.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.config import AGENT_SELF_MANAGE_ENABLED

from ..base_react_agent import BaseReActAgent, _extract_model_name
from ..prompt_registry import PromptRegistry

TAIPEI_TZ = timezone(timedelta(hours=8))


# ============================================================================
# Model-aware tool enforcement（F1 — 學 Hermes TOOL_USE_ENFORCEMENT_MODELS）
#
# Hermes prompt_builder.py 對 tool enforcement 不是 universal 注入，而是只對
# 遵從性差的模型家族（gpt-5/codex/grok/glm/qwen/deepseek 等）才注入強制規則。
#
# 對 tool-native 模型（nemotron/claude/gemini），tool calling 是顯式訓練目標
# （見 NVIDIA Nemotron 3 blog），universal 注入反而會稀釋 prompt 重點、
# 增加雜訊。我們對這些模型跳過 tool_use_enforcement 區塊。
#
# 配對邏輯：模型名 substring match（不分大小寫）。
# 例：nvidia/nemotron-3-super → match "nemotron"
# ============================================================================

# 這些模型家族的 tool calling 是顯式訓練目標，不需要強 enforcement。
# 參考 NVIDIA Nemotron 3 blog + Anthropic/OpenAI/Google 官方 docs。
_TOOL_NATIVE_MODEL_MARKERS: tuple[str, ...] = (
    "nemotron",  # NVIDIA Nemotron 3 (Super/Nano/Ultra)
    "claude",  # Anthropic Claude（tool use 是核心能力）
    "gemini",  # Google Gemini（function calling 原生支援）
    "gpt-4o",  # OpenAI GPT-4o（function calling 訓練充分）
    "gpt-5",  # OpenAI GPT-5
    "o1",  # OpenAI o1 系列
    "o3",  # OpenAI o3 系列
)


def _is_tool_native_model(llm: Any) -> bool:
    """判斷目前 LLM client 是否為 tool-native 模型（不需強 enforcement）。

    Args:
        llm: CryptoMindAgent.llm（可能是 LanguageAwareLLM 或底層 client）。
            測試場景用 __new__() 跳過 __init__ 時可能為 None 或不存在。

    Returns:
        True 若模型屬於 _TOOL_NATIVE_MODEL_MARKERS 中的家族。
        無法判斷（llm=None / 無 model name）時保守 return False（保留 enforcement）。
    """
    if llm is None:
        return False
    try:
        model_name = (_extract_model_name(llm) or "").lower()
        return any(marker in model_name for marker in _TOOL_NATIVE_MODEL_MARKERS)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        # 無法判斷模型 → 保守地不跳過（保留 enforcement）
        return False


def _current_time_line(language: str) -> str:
    """模型必須知道今天日期才能判斷資訊新舊（訓練記憶/舊文章 vs 現況）。"""
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    if language == "zh-CN":
        return f"当前时间：{now}（台湾时间 UTC+8）。你的训练数据有截止日期，一切以此时间为基准。"
    if language == "ru":
        return (
            f"Текущее время: {now} (тайваньское время, UTC+8). "
            "Ваши обучающие данные имеют дату отсечения; считайте эту метку единственным источником истины для «сейчас»."
        )
    if language.startswith("zh"):
        return f"現在時間：{now}（台灣時間 UTC+8）。你的訓練資料有截止日期，一切以此時間為基準。"
    return (
        f"Current time: {now} (Taiwan Time, UTC+8). "
        "Your training data has a cutoff date; treat this timestamp as the single source of truth for 'now'."
    )


def _mask_wallet(address: str) -> str:
    """錢包地址脫敏：前綴 + 末 4 碼（AGENTS.md：不得把真實值放進 prompt）。"""
    if not address or len(address) < 8:
        return ""
    return f"{address[:4]}...{address[-4:]}"


def _user_identity_line(
    language: str,
    display_name: Optional[str],
    wallet_address: Optional[str],
    tier: Optional[str],
    evm_address: Optional[str] = None,
) -> str:
    """使用者身份錨點 — Trustworthy AI Hackathon Principal 支柱。

    讓 Agent 知道「正在跟誰對話」：已驗證的錢包持有者 + 其暱稱。
    Agent 據此可自然稱呼對方、但不洩漏完整錢包地址（邊界設計）。

    路線 D（docs/plans/2026-08-11-trust-evm-onchain-signals-design.md）：
    泛化成鏈中立——TON 地址顯示「已驗證錢包（ton_proof）」，
    EVM 地址顯示「已驗證錢包（EVM 簽名綁定）」。不再寫死 "TON wallet"。

    任一欄位缺失（測試 __new__ 跳過 __init__ 的情境）→ 回空字串，
    不破壞既有 prompt governance 測試（簡繁字元檢查套用到完整 prompt）。
    """
    has_name = bool(display_name and display_name.strip())
    has_wallet = bool(wallet_address)
    has_evm = bool(evm_address)
    if not has_name and not has_wallet and not has_evm:
        return ""

    name_hint = display_name.strip() if has_name else ""
    wallet_hint = _mask_wallet(wallet_address) if has_wallet else ""
    evm_hint = _mask_wallet(evm_address) if has_evm else ""
    tier_hint = tier or "free"

    # 決定錢包描述——依地址格式分派（TON vs EVM），鏈中立用語
    if has_wallet:
        from core.onchain.addresses import is_ton_address

        is_ton = is_ton_address(wallet_address)
        if language == "zh-CN":
            wallet_desc = f"已验证钱包 {wallet_hint}（通过{'ton_proof' if is_ton else 'EVM 签名'}验证）。"
        elif language == "en":
            wallet_desc = f"verified wallet {wallet_hint} ({'ton_proof' if is_ton else 'EVM signature'} verified). "
        elif language == "ru":
            wallet_desc = f"верифицированный кошелёк {wallet_hint} ({'ton_proof' if is_ton else 'EVM-подпись'}). "
        else:
            wallet_desc = f"已驗證錢包 {wallet_hint}（通過{'ton_proof' if is_ton else 'EVM 簽名'}驗證）。"
    else:
        wallet_desc = ""

    # EVM 地址作為附註（主身份是 TON；若只有 EVM 則顯示 EVM）
    evm_desc = ""
    if has_evm and not has_wallet:
        if language == "zh-CN":
            evm_desc = f"已验证钱包 {evm_hint}（通过 EVM 签名绑定）。"
        elif language == "en":
            evm_desc = f"verified wallet {evm_hint} (EVM signature bound). "
        elif language == "ru":
            evm_desc = f"верифицированный кошелёк {evm_hint} (EVM-подпись). "
        else:
            evm_desc = f"已驗證錢包 {evm_hint}（通過 EVM 簽名綁定）。"

    if language == "zh-CN":
        parts = ["你正在与一位已验证的使用者对话。"]
        if name_hint:
            parts.append(f"暱称：{name_hint}。")
        if wallet_desc:
            parts.append(wallet_desc)
        elif evm_desc:
            parts.append(evm_desc)
        parts.append(f"会员等级：{tier_hint}。")
        parts.append("回应时可自然以暱称呼对方，但绝不可透露完整钱包地址。")
        return "当前使用者身份：" + "".join(parts)
    if language == "en":
        parts = ["You are talking to a verified user. "]
        if name_hint:
            parts.append(f"Nickname: {name_hint}. ")
        if wallet_desc:
            parts.append(wallet_desc)
        elif evm_desc:
            parts.append(evm_desc)
        parts.append(f"Membership tier: {tier_hint}. ")
        parts.append(
            "You may address them by nickname naturally, but never reveal the full wallet address."
        )
        return "".join(parts)
    if language == "ru":
        parts = ["Вы общаетесь с проверенным пользователем. "]
        if name_hint:
            parts.append(f"Ник: {name_hint}. ")
        if wallet_desc:
            parts.append(wallet_desc)
        elif evm_desc:
            parts.append(evm_desc)
        parts.append(f"Уровень: {tier_hint}. ")
        parts.append(
            "Можно обращаться по нику, но никогда не раскрывайте полный адрес кошелька."
        )
        return "".join(parts)
    # zh-TW 預設
    parts = ["你正在與一位已驗證的使用者對話。"]
    if name_hint:
        parts.append(f"暱稱：{name_hint}。")
    if wallet_desc:
        parts.append(wallet_desc)
    elif evm_desc:
        parts.append(evm_desc)
    parts.append(f"會員等級：{tier_hint}。")
    parts.append("回應時可自然以暱稱呼對方，但絕不可透露完整錢包地址。")
    return "目前使用者身份：" + "".join(parts)


# 各語言的「base 角色描述」（一句話定位）。market_rules 與 response_quality 走
# PromptRegistry（shared.yaml），此處只保留極短的 base 句，避免在 Python 裡
# 寫死大段 i18n 文字。
_BASE_ROLE = {
    "zh-TW": "你是一個專業的金融市場 AI 分析師。你能夠分析加密貨幣、台股、美股、港股、大宗商品、外匯和宏觀經濟。",
    "zh-CN": "你是一个专业的金融市场 AI 分析师。你能够分析加密货币、台股、美股、港股、大宗商品、外汇和宏观经济。",
    "en": "You are a professional financial market AI analyst. You can analyze cryptocurrencies, TW stocks, US stocks, HK stocks, commodities, forex, and macro economics.",
    "ru": "Вы профессиональный ИИ-аналитик финансовых рынков. Вы можете анализировать криптовалюты, тайваньские акции, американские акции, гонконгские акции, товары, форекс и макроэкономику.",  # TODO: native speaker review
}


# 帳本題專用（core/agents/domain_gate.py）：角色一句話＋誤判時的出口。
# 這輪只開放帳本工具；萬一其實是市場問題，模型要請使用者另外詢問，而不是硬答或亂編。
_LEDGER_ROLE = {
    "zh-TW": "你是 CryptoMind 的記帳與個人帳本助理。你能記錄、查詢、修改、刪除使用者的收支與投資紀錄，並統計損益。",
    "zh-CN": "你是 CryptoMind 的记账与个人账本助理。你能记录、查询、修改、删除用户的收支与投资记录，并统计损益。",
    "en": "You are CryptoMind's bookkeeping and personal-ledger assistant. You can record, query, update and delete the user's income, expense and investment entries, and summarize profit and loss.",
    "ru": "Вы помощник CryptoMind по учёту и личной бухгалтерии. Вы можете записывать, искать, изменять и удалять доходы, расходы и инвестиционные записи пользователя и считать прибыль и убытки.",
}
_LEDGER_SCOPE_NOTE = {
    "zh-TW": "## 本輪範圍\n這一輪只開放帳本工具。若使用者其實想查市場行情、新聞或做分析，請簡短說明並請他直接另外詢問（例如「BTC 現在多少」），不要編造市場數據。",
    "zh-CN": "## 本轮范围\n这一轮只开放账本工具。若用户其实想查市场行情、新闻或做分析，请简短说明并请他直接另外询问（例如“BTC 现在多少”），不要编造市场数据。",
    "en": "## Scope of this turn\nOnly ledger tools are available this turn. If the user actually wants market prices, news or analysis, say so briefly and ask them to ask separately (e.g. \"BTC price now\"); never invent market data.",
    "ru": "## Область этого хода\nВ этом ходе доступны только инструменты учёта. Если пользователь на самом деле хочет рыночные цены, новости или анализ, коротко скажите об этом и попросите задать вопрос отдельно; не выдумывайте рыночные данные.",
}


class CryptoMindAgent(BaseReActAgent):
    """
    Single agent with all tools — CLAW pattern.

    The LLM reads tool descriptions + skills index and decides
    which tools to call. No pre-routing needed.
    """

    name = "cryptomind"
    match_all_skills = True  # 全能 agent：注入 skill 目錄，模型自行 load_skill

    def _get_system_prompt(self, language: str) -> str:
        """組合 system prompt：時間錨點 + base 角色 + 所有 shared.yaml protocol。

        i18n 來源：
        - 時間錨點 + base 角色：本檔（極短字串）
        - 各 protocol：PromptRegistry（shared.yaml 4 語齊全）

        2026-07-19 強化（兩階段）：
        - **第一階段（A1）**：注入此前被遺漏的 5 個 protocol
          (symbol_resolution_protocol / tool_failure_handling / tool_use_enforcement /
          data_freshness / citation_rules) + 新增的 cross_market_routing。
        - **第二階段（F1）**：學 Hermes `TOOL_USE_ENFORCEMENT_MODELS` 白名單，
          對 tool-native 模型（nemotron/claude/gemini/gpt-4o/gpt-5/o1/o3）跳過
          tool_use_enforcement 區塊。這些模型的 tool calling 是顯式訓練目標，
          universal 注入反而稀釋 prompt 重點。

        若新增支援語言，shared.yaml 必須同步補齊，否則
        tests/test_prompt_governance.py 會 fail。
        """
        base = _BASE_ROLE.get(language, _BASE_ROLE["zh-TW"])
        # 按邏輯順序組合：時間 → 使用者身份（Principal） → 能力宣告 → 路由 → 各協定 → 回應品質
        # getattr(None) 預設：測試用 __new__() 跳過 __init__ 時 self 沒設這些 attr
        sections = [
            _current_time_line(language),
            _user_identity_line(
                language,
                display_name=getattr(self, "display_name", None),
                wallet_address=getattr(self, "wallet_address", None),
                tier=getattr(self, "user_tier", None),
                evm_address=getattr(self, "evm_address", None),
            ),
            base,
            # output_language 必須緊跟 base role（最高優先級）。
            # Bug#2 修復：base_react_agent.execute_streaming 拆掉 LanguageAwareLLM
            # wrapper 後，輸出語言全靠這段 system prompt 控制。
            PromptRegistry.get("shared", "output_language", language),
            PromptRegistry.get("shared", "market_rules", language),
            # 未知資產協定（閃迪 bug 根因）：shared.yaml 標明要排在 routing 之前，
            # 讓「不確定市場別就先查證」壓過 routing 的「直接用工具」——
            # 以前寫好了卻沒注入，等於不存在
            PromptRegistry.get("shared", "unknown_asset_protocol", language),
            PromptRegistry.get("shared", "cross_market_routing", language),
            PromptRegistry.get("shared", "symbol_resolution_protocol", language),
            PromptRegistry.get("shared", "tool_failure_handling", language),
        ]
        # F1: tool_use_enforcement 只對「遵從性差的模型家族」注入。
        # tool-native 模型（nemotron/claude/gemini/gpt-4o/gpt-5/o-series）跳過。
        # getattr 預設 None：測試用 __new__() 跳過 __init__ 時可能沒設 self.llm。
        llm = getattr(self, "llm", None)
        if not _is_tool_native_model(llm):
            sections.append(
                PromptRegistry.get("shared", "tool_use_enforcement", language)
            )
        sections.extend(
            [
                PromptRegistry.get("shared", "data_freshness", language),
                PromptRegistry.get("shared", "citation_rules", language),
                # skill 的 Output Format 是版面示意，模型照抄會產出 XX,XXX 這種
                # 佔位符；缺資料時更可能自行編一個看起來合理的數字。
                PromptRegistry.get("shared", "no_placeholder_output", language),
                PromptRegistry.get("shared", "response_quality", language),
                # 判斷/預測型問題的處理指引 — 打破 data_freshness +
                # citation_rules + response_quality 對「無法用工具精確回答」
                # 問題的死結（弱模型會回空）。
                PromptRegistry.get("shared", "judgment_questions", language),
                # 範圍釐清指引 — 教模型面對「台股/美股是否值得投資」「我想要投資」
                # 這類標的未確定的範圍問題時，先釐清指什麼再回答，而非偷換成
                # 最知名個股（如把「台股」答成「台積電」）。與 judgment_questions
                # 互補：後者處理已確定標的的判斷。rule 後衛
                # (should_clarify_wrong_scope) 是這段 prompt 的保險。
                PromptRegistry.get("shared", "scope_clarification", language),
                # 主動記憶指引 — 教模型使用者說出偏好/背景/持倉時，呼叫 remember
                # 工具記住（學 Hermes write_memory）。記住的資訊下次對話自動帶入
                # （MemoryStore 注入 system prompt）。合規：PII 過濾在工具層。
                PromptRegistry.get("shared", "proactive_memory", language),
                # 記憶身份宣告（2026-09-14 根絕「我沒有跨對話記憶」幻覺）：
                # 明告模型平台有記憶系統；沒看到「## 用戶記憶」區塊＝還沒存東西，
                # 不是沒有能力；被問記憶問題先查 list_my_skills_memory。
                PromptRegistry.get("shared", "memory_identity", language),
                # 記憶可見（2026-09-12 留存核心第 4 項）：引用記憶／帳本時標來源與日期
                PromptRegistry.get("shared", "memory_citation", language),
                # 合規口吻（2026-09-14）：描述不指令、不給個人化買賣建議、非託管
                PromptRegistry.get("shared", "analysis_not_advice", language),
                # Skill / Memory 自主管理指引 — 教模型何時呼叫 propose_custom_skill
                # 與 list_my_skills_memory。沒這段，工具即使註冊 LLM 也不會主動用
                # （會直接寫文字回答），導致 consent gate 永遠不觸發（PR #429 缺的指引）。
                # 2026-09-12 prompt 分層第一步：旗標關著時工具根本沒註冊，這段
                # 指引（zh 1.3K／en 2.3K 字元）只是在教模型呼叫不存在的工具。
                PromptRegistry.get("shared", "self_manage_skills", language)
                if AGENT_SELF_MANAGE_ENABLED
                else "",
                # 多鏈偵測指引 — 教模型在非 Ethereum 查詢時傳正確 chain_id。
                # Etherscan V2 / GoPlus 都支援多鏈，但預設 1=Ethereum。沒這段
                # agent 永遠查 Ethereum mainnet → 非 Ethereum 查詢結果無意義。
                PromptRegistry.get("shared", "multichain_awareness", language),
            ]
        )
        # 濾掉空字串（避免連續 \n\n\n）
        return "\n\n".join(s for s in sections if s)

    def _get_system_prompt_for_domain(self, language: str, domain: str) -> str:
        """題型專屬 system prompt。目前只有「帳本」：不載入市場分析區塊（市場規則、未知資產、
        跨市場路由、symbol 解析、資料時效、引用規則、判斷／範圍釐清、主動記憶、多鏈…約一半的字數），
        保留語言、tool_use_enforcement、**response_quality（內含 record_entry／query_ledger 規則）**、
        no_placeholder_output、memory_citation、analysis_not_advice。其他題型回一般版。
        """
        from ..domain_gate import DOMAIN_LEDGER

        if domain != DOMAIN_LEDGER:
            return self._get_system_prompt(language)
        sections = [
            _current_time_line(language),
            _user_identity_line(
                language,
                display_name=getattr(self, "display_name", None),
                wallet_address=getattr(self, "wallet_address", None),
                tier=getattr(self, "user_tier", None),
                evm_address=getattr(self, "evm_address", None),
            ),
            _LEDGER_ROLE.get(language, _LEDGER_ROLE["zh-TW"]),
            PromptRegistry.get("shared", "output_language", language),
        ]
        llm = getattr(self, "llm", None)
        if not _is_tool_native_model(llm):
            sections.append(PromptRegistry.get("shared", "tool_use_enforcement", language))
        sections.extend(
            [
                PromptRegistry.get("shared", "no_placeholder_output", language),
                PromptRegistry.get("shared", "response_quality", language),
                PromptRegistry.get("shared", "memory_citation", language),
                PromptRegistry.get("shared", "analysis_not_advice", language),
                _LEDGER_SCOPE_NOTE.get(language, _LEDGER_SCOPE_NOTE["zh-TW"]),
            ]
        )
        return "\n\n".join(s for s in sections if s)

    def _inject_tool_retry_instructions(self, system_prompt: str, language: str) -> str:
        """覆寫 base 類：CryptoMindAgent 的 _get_system_prompt 已自行組裝所有 protocol。

        **為何覆寫成 no-op**：
        - base 類的 `_inject_tool_retry_instructions` 會無條件注入 5 個 section
          (symbol_resolution_protocol / tool_failure_handling / data_freshness /
          citation_rules / tool_use_enforcement)。
        - 但 CryptoMindAgent._get_system_prompt 已經把這些都組進去了（A1 強化），
          所以 base 類會**重複注入**（pre-existing bug）。
        - 更重要：base 類不認識 F1 model-aware 邏輯，會把 nemotron 該跳過的
          tool_use_enforcement 又塞回去，**完全蓋過 F1**。

        因此 CryptoMindAgent 覆寫成 no-op，所有 prompt 組裝集中在 _get_system_prompt。
        """
        return system_prompt
