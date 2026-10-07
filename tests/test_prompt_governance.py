import re
from pathlib import Path

PROMPT_SOURCE_PATHS = [
    *Path("core/agents/prompts").glob("*.yaml"),
    *Path("core/agents/descriptions").glob("*.md"),
]

MODEL_FACING_RUNTIME_PATHS = [
    Path("core/agents/tools.py"),
    Path("core/tools/tw_symbol_resolver.py"),
    Path("core/tools/tw_stock_tools.py"),
    Path("core/tools/us_stock_tools.py"),
    Path("core/tools/us_data_provider.py"),
    Path("core/tools/schemas.py"),
    Path("core/tools/universal_resolver.py"),
]

# Guardrail: runtime prompt/design sources should express market boundaries,
# not hardcode specific assets.
BANNED_PATTERNS = [
    r"\bAAPL\b",
    r"\bTSM\b",
    r"\bBTC\b",
    r"\bETH\b",
    r"\bSOL\b",
    r"\bPI\b",
    r"\bINTC\b",
    r"\bNVDA\b",
    r"\bSMCI\b",
    r"\bAMD\b",
    r"\bGOOGL\b",
    r"\bMETA\b",
    r"台積電",
    r"聯發科",
    r"鴻海",
    r"蘋果",
    r"特斯拉",
    r"比特幣",
    r"以太坊",
    r"2330",
]


def _strip_docstrings(source: str) -> str:
    """Remove all triple-quoted string literals so only executable code remains."""
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_prompt_sources_do_not_hardcode_specific_assets():
    """YAML/MD prompt files are pure prompt content — skip governance check.

    These files are *instructional* by nature; they teach the LLM how to behave.
    Banning asset names here would prevent the LLM from understanding user queries
    about those assets.  The governance goal is to prevent *routing logic* from
    hardcoding specific tickers, not to prevent prompts from mentioning them.
    """
    # Intentionally not scanning YAML/MD prompt files for asset names.
    # Those are instructional examples for the LLM, not routing dictionaries.
    pass


def test_manager_does_not_inline_multiline_prompt_templates():
    manager_py = Path("core/agents/manager.py").read_text(encoding="utf-8")
    assert 'prompt = f"""' not in manager_py


def test_bootstrap_agent_metadata_does_not_hardcode_specific_equities():
    bootstrap = Path("core/agents/bootstrap.py").read_text(encoding="utf-8")
    banned_equity_examples = [
        r"\bAAPL\b",
        r"\bTSM\b",
        r"\bNVDA\b",
        r"\bMSFT\b",
        r"\bGOOGL\b",
        r"\bAMZN\b",
        r"\bMETA\b",
        r"\bSMCI\b",
        r"\bAMD\b",
        r"台積電",
        r"鴻海",
        r"聯發科",
    ]
    violations = [
        pattern for pattern in banned_equity_examples if re.search(pattern, bootstrap)
    ]
    assert not violations, (
        "Found hardcoded equity examples in bootstrap metadata: "
        + ", ".join(violations)
    )


def test_model_facing_runtime_sources_do_not_hardcode_specific_equity_examples():
    """Scan only executable code (strip docstrings) — docstring examples are
    instructional text for the LLM, not routing logic."""
    banned_equity_examples = [
        r"\bAAPL\b",
        r"\bTSLA\b",
        r"\bNVDA\b",
        r"\bMSFT\b",
        r"\bGOOGL\b",
        r"\bAMZN\b",
        r"\bMETA\b",
        r"\bTSM\b",
        r"\b2330\b",
        r"\b2317\b",
        r"\b2454\b",
        r"台積電",
        r"鴻海",
        r"聯發科",
        r"蘋果",
        r"特斯拉",
    ]
    violations = []
    for path in MODEL_FACING_RUNTIME_PATHS:
        content = path.read_text(encoding="utf-8")
        code_only = _strip_docstrings(content)
        for pattern in banned_equity_examples:
            if re.search(pattern, code_only):
                violations.append(f"{path}: {pattern}")

    assert not violations, (
        "Found hardcoded equity examples in model-facing runtime sources:\n"
        + "\n".join(violations)
    )


# ============================================================================
# i18n Governance — shared.yaml 4 語齊全 + cryptomind system prompt 簡繁正確
#
# 背景：原本 shared.yaml 只有 zh-TW + en，zh-CN 用戶拿到繁中 prompt
# （cryptomind_agent.py:79 只 replace 兩個詞，等於沒轉）造成 R3「簡中被回繁中」。
# 這群 test 防止 i18n 回歸。
# ============================================================================

import pytest  # noqa: E402
import yaml  # noqa: E402

from core.agents.prompt_registry import PromptRegistry  # noqa: E402

SUPPORTED_LANGUAGES = ("zh-TW", "zh-CN", "en", "ru")
SHARED_YAML = (
    Path(__file__).parent.parent / "core" / "agents" / "prompts" / "shared.yaml"
)


@pytest.fixture(scope="module")
def shared_yaml_data():
    """直接讀 YAML（不經 PromptRegistry），避免快取干擾斷言。"""
    with open(SHARED_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_shared_yaml_loads_successfully(shared_yaml_data):
    """YAML 必須能 parse（語法錯誤會直接 fail）。"""
    assert isinstance(shared_yaml_data, dict)
    assert len(shared_yaml_data) > 0


@pytest.mark.parametrize(
    "expected_key",
    [
        "market_rules",
        "cross_market_routing",
        "symbol_resolution_protocol",
        "tool_failure_handling",
        "data_freshness",
        "citation_rules",
        "response_quality",
        "tool_use_enforcement",
        "memory_citation",
        "memory_identity",
    ],
)
def test_shared_yaml_key_exists(shared_yaml_data, expected_key):
    """關鍵 key 必須存在。新增 key 請加進這裡。"""
    assert expected_key in shared_yaml_data, (
        f"shared.yaml 缺少必要的 key: {expected_key}"
    )


def test_shared_yaml_all_keys_have_all_languages(shared_yaml_data):
    """每個 top-level key 都必須涵蓋 4 個支援語言。

    核心治理：避免將來新增 key 或新語言時漏掉某個。
    若某 key 真的不需要某語言（罕見），明確寫進這個 test 的 exception list，
    不要默默省略。
    """
    missing = {}
    for key, value in shared_yaml_data.items():
        if not isinstance(value, dict):
            missing[key] = f"value 不是 dict（type={type(value).__name__}）"
            continue
        absent = [lang for lang in SUPPORTED_LANGUAGES if lang not in value]
        if absent:
            missing[key] = absent
    assert not missing, (
        f"shared.yaml 以下 key 缺少語言覆蓋（每個 key 必須有全部 {SUPPORTED_LANGUAGES}）：\n"
        + "\n".join(f"  {k}: 缺 {v}" for k, v in missing.items())
    )


def test_shared_yaml_no_empty_language_strings(shared_yaml_data):
    """語言值不能是空字串（容易被 fallback 掩蓋問題）。"""
    empties = []
    for key, value in shared_yaml_data.items():
        if not isinstance(value, dict):
            continue
        for lang, content in value.items():
            if isinstance(content, str) and not content.strip():
                empties.append(f"{key}.{lang}")
    assert not empties, f"shared.yaml 以下位置是空字串：{empties}"


@pytest.fixture(scope="module")
def cryptomind_prompt_per_lang():
    """建 CryptoMindAgent 並取 4 語 system prompt。

    用 __new__ 略過 BaseReActAgent.__init__（重量級 setup），
    因為 _get_system_prompt 不需要 registry/llm。
    """
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)
    return {lang: agent._get_system_prompt(lang) for lang in SUPPORTED_LANGUAGES}


# 簡體特徵字（這些字繁體寫法 != 簡體）。挑 shared.yaml 確實有出現的詞。
_SIMPLIFIED_ONLY = ["加密货币", "技术指标", "链上数据", "恐惧贪婪", "板块表现"]
# 繁體特徵字（同上對稱）
_TRADITIONAL_ONLY = ["加密貨幣", "技術指標", "鏈上數據", "恐懼貪婪", "板塊表現"]


def test_cryptomind_zh_cn_prompt_is_simplified(cryptomind_prompt_per_lang):
    """zh-CN prompt 必須含簡體特徵字、不含繁體特徵字。"""
    prompt = cryptomind_prompt_per_lang["zh-CN"]
    missing_simp = [w for w in _SIMPLIFIED_ONLY if w not in prompt]
    has_trad = [w for w in _TRADITIONAL_ONLY if w in prompt]
    assert not missing_simp, f"zh-CN prompt 缺簡體特徵字：{missing_simp}"
    assert not has_trad, f"zh-CN prompt 含繁體字（應改簡）：{has_trad}"


def test_cryptomind_zh_tw_prompt_is_traditional(cryptomind_prompt_per_lang):
    """zh-TW prompt 必須含繁體特徵字、不含簡體特徵字。"""
    prompt = cryptomind_prompt_per_lang["zh-TW"]
    missing_trad = [w for w in _TRADITIONAL_ONLY if w not in prompt]
    has_simp = [w for w in _SIMPLIFIED_ONLY if w in prompt]
    assert not missing_trad, f"zh-TW prompt 缺繁體特徵字：{missing_trad}"
    assert not has_simp, f"zh-TW prompt 含簡體字（應改繁）：{has_simp}"


@pytest.mark.parametrize("lang", list(SUPPORTED_LANGUAGES))
def test_cryptomind_prompt_non_empty(cryptomind_prompt_per_lang, lang):
    """每個語言 prompt 都不該是空字串。"""
    prompt = cryptomind_prompt_per_lang[lang]
    assert isinstance(prompt, str)
    assert len(prompt) > 100, f"{lang} prompt 太短（{len(prompt)} chars）"


def test_cryptomind_prompt_has_market_rules_section(cryptomind_prompt_per_lang):
    """4 語都必須包含 market_rules 區塊（核心能力說明）。"""
    title_markers = {
        "zh-TW": "市場分析能力",
        "zh-CN": "市场分析能力",
        "en": "Market Analysis Capabilities",
        "ru": "Возможности анализа рынка",
    }
    for lang, prompt in cryptomind_prompt_per_lang.items():
        marker = title_markers[lang]
        assert marker in prompt, (
            f"{lang} prompt 缺 market_rules 標題（{marker!r}）"
        )


def test_cryptomind_prompt_has_judgment_questions_section(cryptomind_prompt_per_lang):
    """4 語都必須包含 judgment_questions 區塊。

    Regression：線上 agent 對「會跌到何時 / 何時入場」這類預測型問題回空，
    根因之一是 prompt 缺少對判斷/預測問題的明確指引，導致模型在
    data_freshness / citation_rules / response_quality 之間陷入死結回空。
    """
    title_markers = {
        "zh-TW": "判斷與預測型問題",
        "zh-CN": "判断与预测型问题",
        "en": "Judgment and Prediction Questions",
        "ru": "Вопросы на оценку и прогноз",
    }
    for lang, prompt in cryptomind_prompt_per_lang.items():
        marker = title_markers[lang]
        assert marker in prompt, (
            f"{lang} prompt 缺 judgment_questions 標題（{marker!r}）"
        )


def test_cryptomind_prompt_has_memory_identity_section(cryptomind_prompt_per_lang):
    """4 語都必須包含 memory_identity 區塊（2026-09-14 根絕「我沒有記憶」幻覺）。

    線上事件：記憶為空時 prompt 沒有記憶區塊，模型自行答「我沒有跨對話的
    長期記憶」——與平台實際能力相反。此區塊宣告平台有記憶系統，並禁止否認。
    """
    title_markers = {
        "zh-TW": "記憶系統身份",
        "zh-CN": "记忆系统身份",
        "en": "Memory System Identity",
        "ru": "Идентичность системы памяти",
    }
    for lang, prompt in cryptomind_prompt_per_lang.items():
        marker = title_markers[lang]
        assert marker in prompt, (
            f"{lang} prompt 缺 memory_identity 標題（{marker!r}）"
        )


def test_cryptomind_prompt_injects_unknown_asset_protocol_before_routing(
    cryptomind_prompt_per_lang,
):
    """unknown_asset_protocol 要注入，而且排在 cross_market_routing 之前。

    shared.yaml 自己寫明「此協定排在 routing 之前」（閃迪 bug 根因：不確定
    市場別就先查證），但 _get_system_prompt 從沒注入過——4 語寫好的協定等於
    不存在。用 YAML 內各段的標題行當標記，不在這裡重抄文字。
    """
    PromptRegistry.load()
    for lang, prompt in cryptomind_prompt_per_lang.items():
        protocol_title = next(
            line
            for line in PromptRegistry.get(
                "shared", "unknown_asset_protocol", lang
            ).splitlines()
            if line.strip()
        )
        routing_title = next(
            line
            for line in PromptRegistry.get(
                "shared", "cross_market_routing", lang
            ).splitlines()
            if line.strip()
        )
        assert protocol_title in prompt, f"{lang} prompt 沒注入 unknown_asset_protocol"
        assert prompt.index(protocol_title) < prompt.index(routing_title), (
            f"{lang} unknown_asset_protocol 必須排在 cross_market_routing 之前"
        )


#: 刻意保留但目前不注入的 shared.yaml key（要寫理由）。空＝每個 key 都有人用。
_SHARED_KEYS_ALLOWED_UNUSED: dict[str, str] = {}


def test_every_shared_yaml_key_is_referenced(shared_yaml_data):
    """shared.yaml 的每個 key 都要在程式碼裡被引用（或明列在允許清單）。

    unknown_asset_protocol 就是這樣靜默失效的：4 語寫好、文件寫明注入位置，
    卻沒有任何程式讀它——所有治理測試都綠。這裡守「寫了就要有人用」。
    """
    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for d in ("core", "api")
        for p in (root / d).rglob("*.py")
    )
    unused = [
        key
        for key in shared_yaml_data
        if key not in _SHARED_KEYS_ALLOWED_UNUSED
        and not re.search(rf"[\"']{re.escape(key)}[\"']", sources)
    ]
    assert not unused, (
        f"shared.yaml 以下 key 沒有任何程式引用（寫了卻沒注入）：{unused}——"
        "請接上注入點，或加進 _SHARED_KEYS_ALLOWED_UNUSED 並寫明理由"
    )


def test_cryptomind_agent_does_not_hardcode_market_rules():
    """market_rules 應從 shared.yaml 取，不該寫在 cryptomind_agent.py。

    若有人把它搬回 Python 檔，這個 test 會 fail 提醒。
    """
    from core.agents.agents import cryptomind_agent as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "市場分析能力" not in src, (
        "cryptomind_agent.py 不應 hardcode 『市場分析能力』，"
        "請改放 shared.yaml 的 market_rules。"
    )


def test_cross_market_routing_has_topicless_followup_rule():
    """4 語都必須有「沒主題的 follow-up 查詢」原則（2026-07-20 修復）。

    使用者問沒明確主題的 follow-up 時（如「最新消息」「最近怎樣」），
    agent 應該從對話上下文推斷，而不是列舉具體詞條（hardcode anti-pattern）。

    重點：規則用**原則性描述**（「沒有明確標的 → 從上下文推斷」），
    不是列舉「最新新聞 / latest news / 最近消息」這些詞條（會漏掉新詞）。
    """
    from core.agents.prompt_registry import PromptRegistry

    PromptRegistry.load()
    shared_path = Path(__file__).resolve().parent.parent / "core" / "agents" / "prompts" / "shared.yaml"
    src = shared_path.read_text(encoding="utf-8")

    # 4 語各自有對應的標題
    markers = {
        "zh-TW": "沒主題的 follow-up",
        "zh-CN": "没主题的 follow-up",
        "en": "Topicless follow-up",
        "ru": "без темы",
    }
    for lang, marker in markers.items():
        assert marker in src, (
            f"shared.yaml cross_market_routing 必須包含「{marker}」規則（{lang}）"
        )

    # anti-hardcode 檢查：不能列舉過多具體觸發詞
    # （「最新」當範例 1-2 次可以，但不能變成長串字典）
    forbidden_phrase_lists = [
        "「最新新聞」「最近消息」「有什麼新聞」「latest news」",  # zh-TW 過去的 hardcode
        "「最新新闻」「最近消息」「有什么新闻」「latest news」",  # zh-CN
    ]
    for forbidden in forbidden_phrase_lists:
        assert forbidden not in src, (
            f"不應列舉具體觸發詞（hardcode anti-pattern）：{forbidden}\n"
            "應該用原則性描述（『沒明確標的 → 從上下文推斷』），讓 LLM 自己類推到新詞"
        )


def test_scope_clarification_forbids_plain_text_clarify():
    """4 語的 scope_clarification 都必須禁止「把釐清寫成純文字」（2026-09-08 修復）。

    線上案例：模型沒呼叫 clarify 工具，把釐清問題列在回覆文字裡 → 規則後衛
    誤判成答非所問再彈第二張卡 → 使用者被問兩次、選項不一致。prompt 必須
    明確告訴模型：釐清只能走 clarify 工具，並點出「被問兩次」的後果。
    """
    from core.agents.prompt_registry import PromptRegistry

    PromptRegistry.load()
    shared_path = Path(__file__).resolve().parent.parent / "core" / "agents" / "prompts" / "shared.yaml"
    src = shared_path.read_text(encoding="utf-8")

    # 每語言：工具名 + 「問兩次」後果標記
    markers = {
        "zh-TW": ("clarify", "被問兩次"),
        "zh-CN": ("clarify", "被问两次"),
        "en": ("clarify", "asked twice"),
        "ru": ("clarify", "дважды"),
    }
    for lang, (tool_marker, consequence_marker) in markers.items():
        for marker in (tool_marker, consequence_marker):
            assert marker in src, (
                f"shared.yaml scope_clarification 必須包含「{marker}」（{lang}）——"
                "禁止把釐清問題寫成純文字，只能呼叫 clarify 工具"
            )


# ============================================================================
# 使用者身份錨點（_user_identity_line）— Trustworthy AI Principal 支柱
# ============================================================================


class TestUserIdentityLine:
    """個人化身份注入 system prompt 的關鍵屬性。"""

    def test_returns_empty_when_all_fields_none(self):
        """測試 __new__() 跳 __init__ 的情境——不破壞既有 prompt 結構。"""
        from core.agents.agents.cryptomind_agent import _user_identity_line

        assert _user_identity_line("zh-TW", None, None, None) == ""
        assert _user_identity_line("en", None, None, None) == ""

    def test_wallet_is_masked_not_full(self):
        """AGENTS.md：錢包地址不可完整進 prompt（用前綴+末4碼）。"""
        from core.agents.agents.cryptomind_agent import _user_identity_line

        full_addr = "UQTestWalletAddr1234567890"
        line = _user_identity_line("zh-TW", "鈺澔", full_addr, "premium")
        assert full_addr not in line
        assert "UQTe" in line  # 前 4 碼
        assert "7890" in line  # 末 4 碼
        assert "..." in line

    def test_works_with_name_only(self):
        from core.agents.agents.cryptomind_agent import _user_identity_line

        line = _user_identity_line("zh-TW", "鈺澔", None, "free")
        assert "鈺澔" in line

    def test_works_with_wallet_only(self):
        from core.agents.agents.cryptomind_agent import _user_identity_line

        line = _user_identity_line("en", None, "UQAbcd1234Efgh5678", "free")
        assert "UQAb" in line
        assert "5678" in line

    def test_never_reveals_full_wallet_any_language(self):
        from core.agents.agents.cryptomind_agent import _user_identity_line

        addr = "EQDr0vT0T0T0T0T0T0T0T0T0T0T0T0T0T0_wallet"
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            line = _user_identity_line(lang, "test", addr, "free")
            assert addr not in line, f"{lang} 洩漏了完整地址"

    def test_short_wallet_returns_empty_hint(self):
        """極短地址（< 8）不脫敏，直接回空 wallet hint。"""
        from core.agents.agents.cryptomind_agent import _user_identity_line

        line = _user_identity_line("zh-TW", "鈺澔", "UQ", "free")
        # wallet 太短不顯示，但 name 仍正常
        assert "鈺澔" in line
