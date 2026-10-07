"""
CLAW Loop 強化測試 — 2026-07-19

驗證四項強化：
A. Prompt + Tool 描述強化（cryptomind_agent 注入所有 protocol + cross_market_routing）
B. Token 上限放寬（8192）+ 截斷偵測（finish_reason='length'）
C. BYOK fallback 強化（不預設啟用 + timeout guard + fallback max_tokens）
D. Tool name registry 動態同步 sanitizer

不需要 NVIDIA_API_KEY；全為純 Python 邏輯驗證。
"""
from __future__ import annotations

import pytest

from core.agents.prompt_registry import PromptRegistry

# ============================================================================
# A. Prompt 強化
# ============================================================================


@pytest.fixture(scope="module")
def cryptomind_prompt_per_lang():
    """建 CryptoMindAgent 並取 4 語 system prompt（不跑完整 __init__）。

    注入 mock LLM 帶 nemotron model name（tool-native 模型）。F1 邏輯偵測到此
    會跳過 tool_use_enforcement；如果想驗證「tool-enforcement-required 模型」
    的 prompt，請用另一個 fixture（cryptomind_prompt_per_lang_with_enforcement）。
    """
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeNemotronLLM:
        model_name = "nvidia/nemotron-3-super-120b-a12b"

    agent.llm = _FakeNemotronLLM()
    langs = ("zh-TW", "zh-CN", "en", "ru")
    return {lang: agent._get_system_prompt(lang) for lang in langs}


@pytest.fixture(scope="module")
def cryptomind_prompt_with_enforcement_per_lang():
    """用「需要強 enforcement」的模型（如 qwen）跑 system prompt。

    用於對比驗證 F1：tool-native 模型 vs enforcement-required 模型的 prompt 差異。
    """
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeQwenLLM:
        model_name = "qwen/qwen-2.5-72b-instruct"

    agent.llm = _FakeQwenLLM()
    langs = ("zh-TW", "zh-CN", "en", "ru")
    return {lang: agent._get_system_prompt(lang) for lang in langs}


@pytest.mark.parametrize(
    "protocol_marker_zh_tw",
    [
        "標準代號解析協定",  # symbol_resolution_protocol
        "工具失敗處理流程",  # tool_failure_handling
        "資料時效規則",  # data_freshness
        "引用與來源規則",  # citation_rules
        "跨市場路由提示",  # cross_market_routing（新）
    ],
)
def test_cryptomind_prompt_injects_all_protocols(
    cryptomind_prompt_per_lang, protocol_marker_zh_tw
):
    """A1：所有非 enforcement 的 protocol 都該注入 zh-TW prompt。"""
    assert protocol_marker_zh_tw in cryptomind_prompt_per_lang["zh-TW"], (
        f"cryptomind zh-TW system prompt 缺 protocol：{protocol_marker_zh_tw}。"
        "請檢查 cryptomind_agent._get_system_prompt() 是否注入了對應 shared.yaml key。"
    )


# ============================================================================
# F1: Model-aware tool enforcement（nemotron 跳過，qwen 注入）
# ============================================================================


def test_tool_native_model_skips_tool_use_enforcement(cryptomind_prompt_per_lang):
    """F1：nemotron（tool-native）的 prompt 不該含 tool_use_enforcement。

    學 Hermes TOOL_USE_ENFORCEMENT_MODELS：tool-native 模型的 tool calling
    是顯式訓練目標，universal 注入反而稀釋 prompt 重點。
    """
    # 4 語都不該出現 tool_use_enforcement 標題
    markers = {
        "zh-TW": "工具使用強制規則",
        "zh-CN": "工具使用强制规则",
        "en": "Tool Use Enforcement",
        "ru": "Обязательное использование инструментов",
    }
    for lang, marker in markers.items():
        assert marker not in cryptomind_prompt_per_lang[lang], (
            f"{lang} nemotron prompt 不該含 tool_use_enforcement（F1）：{marker!r}"
        )


def test_enforcement_required_model_includes_tool_use_enforcement(
    cryptomind_prompt_with_enforcement_per_lang,
):
    """F1：qwen（非 tool-native）的 prompt 該含 tool_use_enforcement。"""
    markers = {
        "zh-TW": "工具使用強制規則",
        "zh-CN": "工具使用强制规则",
        "en": "Tool Use Enforcement",
        "ru": "Обязательное использование инструментов",
    }
    for lang, marker in markers.items():
        assert marker in cryptomind_prompt_with_enforcement_per_lang[lang], (
            f"{lang} qwen prompt 該含 tool_use_enforcement（F1）：{marker!r}"
        )


@pytest.mark.parametrize(
    "model_name, expected_native",
    [
        ("nvidia/nemotron-3-super-120b-a12b", True),
        ("nvidia/nemotron-3-nano-omni-30b-a3b", True),
        ("anthropic/claude-sonnet-4", True),
        ("google/gemini-2.5-pro", True),
        ("openai/gpt-4o", True),
        ("openai/gpt-5", True),
        ("openai/o1-preview", True),
        ("openai/o3-mini", True),
        # 非 tool-native 模型 → 需要 enforcement
        ("qwen/qwen-2.5-72b-instruct", False),
        ("deepseek/deepseek-r1", False),
        ("meta-llama/llama-3.3-70b-instruct", False),
        ("unknown", False),
    ],
)
def test_is_tool_native_model_classifier(model_name, expected_native):
    """F1：_is_tool_native_model 分類正確。"""
    from core.agents.agents.cryptomind_agent import _is_tool_native_model

    class _FakeLLM:
        pass

    fake = _FakeLLM()
    fake.model_name = model_name
    assert _is_tool_native_model(fake) is expected_native, (
        f"model={model_name} expected_native={expected_native} 但判錯"
    )


def test_is_tool_native_model_handles_none_llm():
    """F1：llm 無法判讀時保守地 return False（保留 enforcement）。"""

    class _FakeLLM:
        # 沒設 model_name
        pass

    from core.agents.agents.cryptomind_agent import _is_tool_native_model

    # 不該 raise；保守 return False
    assert _is_tool_native_model(_FakeLLM()) is False


# ============================================================================
# G1 整合測試：F1 必須在「完整組裝後的 system prompt」也生效
# （不只 _get_system_prompt，還要通過 _build_agent_system_prompt）
# ============================================================================


def test_g1_f1_takes_effect_through_build_agent_system_prompt_nemotron():
    """G1：nemotron 走完整 _build_agent_system_prompt 後仍不該有 tool_use_enforcement。

    這是 code review 抓到的 P0 bug：CryptoMindAgent 沒覆寫
    _inject_tool_retry_instructions，base 類會無條件注入 5 個 section，
    完全蓋過 F1。此測試在覆寫後必須通過。
    """
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeNemotronLLM:
        model_name = "nvidia/nemotron-3-super-120b-a12b"

    agent.llm = _FakeNemotronLLM()

    # 模擬 _build_agent_system_prompt 的兩步驟
    base_prompt = agent._get_system_prompt("zh-TW")
    final_prompt = agent._inject_tool_retry_instructions(base_prompt, "zh-TW")

    # nemotron 不該看到 tool_use_enforcement
    assert "工具使用強制規則" not in final_prompt, (
        "G1 regression: nemotron 完整 prompt 仍含 tool_use_enforcement。"
        "請檢查 CryptoMindAgent 是否正確覆寫 _inject_tool_retry_instructions。"
    )


def test_g1_no_duplicate_protocol_injection_nemotron():
    """G1：nemotron prompt 不該有重複的 protocol section（dual-injection bug）。

    Pre-existing bug：base 類 _inject_tool_retry_instructions 會把 5 個 section
    重新注入，但 _get_system_prompt 已經注入過 → 同一段文字出現兩次。
    """
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeNemotronLLM:
        model_name = "nvidia/nemotron-3-super-120b-a12b"

    agent.llm = _FakeNemotronLLM()

    base_prompt = agent._get_system_prompt("zh-TW")
    final_prompt = agent._inject_tool_retry_instructions(base_prompt, "zh-TW")

    # cross_market_routing 應只出現一次（標題只一個）
    assert final_prompt.count("跨市場路由提示") == 1, (
        f"protocol 重複注入：跨市場路由提示 出現 "
        f"{final_prompt.count('跨市場路由提示')} 次"
    )
    # symbol_resolution_protocol 也只一次
    assert final_prompt.count("標準代號解析協定") == 1


def test_g1_qwen_still_gets_tool_use_enforcement():
    """G1：qwen（非 tool-native）完整 prompt 仍應含 tool_use_enforcement。"""
    from core.agents.agents.cryptomind_agent import CryptoMindAgent

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeQwenLLM:
        model_name = "qwen/qwen-2.5-72b-instruct"

    agent.llm = _FakeQwenLLM()

    base_prompt = agent._get_system_prompt("zh-TW")
    final_prompt = agent._inject_tool_retry_instructions(base_prompt, "zh-TW")

    assert "工具使用強制規則" in final_prompt


# ============================================================================
# G2: per-request flag reset
# ============================================================================


def test_g2_per_request_flags_reset_on_new_request():
    """G2：_numeric_verified / _clarified 在每個請求開始時重設。

    Review P1 bug：flag 挂在 self 上 + manager LRU cache 重用 = 跨請求殘留。
    第一個請求觸發後，後續請求的 Phase B/C 永久 skip。
    """
    # 這是整合行為測試。直接呼叫 _claw_loop_node 太重（需要 DB、agent_registry），
    # 改測「重設邏輯存在於 _claw_loop_node 開頭」。
    import inspect

    from core.agents.manager import claw_loop

    src = inspect.getsource(claw_loop.ClawLoopMixin._claw_loop_node)
    # 開頭 100 行內要有重設
    head = src[:3000]
    assert "self._numeric_verified = False" in head, (
        "G2: _claw_loop_node 開頭沒重設 _numeric_verified"
    )
    assert "self._clarified = False" in head, (
        "G2: _claw_loop_node 開頭沒重設 _clarified"
    )


# ============================================================================
# G5: fallback recursion_limit 用對常數
# ============================================================================


def test_g5_fallback_recursion_limit_uses_correct_constant():
    """G5：fallback fb_config 的 recursion_limit 應為 MANAGER_GRAPH_RECURSION_LIMIT。

    Review P1 bug：原寫 AGENT_EXECUTION_TIMEOUT=3600（秒），但 LangGraph 的
    recursion_limit 是 step count，不是秒。用錯會讓 graph 可遞迴 3600 步。
    """
    import inspect

    from core.agents.manager import claw_loop

    src = inspect.getsource(claw_loop.ClawLoopMixin._claw_loop_node)
    # 不該再有 recursion_limit: AGENT_EXECUTION_TIMEOUT
    assert "recursion_limit\": AGENT_EXECUTION_TIMEOUT" not in src, (
        "G5 regression: fallback recursion_limit 仍用 AGENT_EXECUTION_TIMEOUT（秒）"
    )
    # 應該用 MANAGER_GRAPH_RECURSION_LIMIT
    assert "MANAGER_GRAPH_RECURSION_LIMIT" in src, (
        "G5: fallback 沒改用 MANAGER_GRAPH_RECURSION_LIMIT"
    )


# ============================================================================
# G6: sanitizer 預編譯 regex 效能
# ============================================================================


def test_g6_sanitizer_patterns_are_cached():
    """G6：編譯過的 regex 應被 cache，第二次呼叫不重建。"""
    from core.agents.tool_name_registry import (
        _get_compiled_sanitizer_patterns,
        register_tool_names,
        reset_for_test,
    )

    reset_for_test()
    register_tool_names({"test_tool_a", "test_tool_b"})

    p1 = _get_compiled_sanitizer_patterns()
    p2 = _get_compiled_sanitizer_patterns()
    # 同一物件 reference（cache hit）
    assert p1 is p2, "G6: sanitizer patterns 沒被 cache"

    reset_for_test()


def test_g6_sanitizer_cache_invalidated_on_register():
    """G6：register_tool_names 後 cache 應失效，下次呼叫重建。"""
    from core.agents.tool_name_registry import (
        _get_compiled_sanitizer_patterns,
        register_tool_names,
        reset_for_test,
    )

    reset_for_test()
    register_tool_names({"tool_a"})
    p1 = _get_compiled_sanitizer_patterns()

    # 新增 tool → invalidate
    register_tool_names({"tool_a", "tool_b"})
    p2 = _get_compiled_sanitizer_patterns()

    # 不同物件（cache 被清掉重建）
    assert p1 is not p2, "G6: register_tool_names 沒 invalidate cache"

    reset_for_test()


def test_g6_strip_tool_name_leaks_strips_all_variants():
    """G6：重構後 sanitizer 仍正確清掉所有泄露 pattern。"""
    from core.agents.manager import claw_loop
    from core.agents.tool_name_registry import register_tool_names, reset_for_test

    reset_for_test()
    # 用唯一 name（避免 substring match 干擾）
    register_tool_names({"zz_uniq_a"})

    # 5 種 pattern 都該清
    cases = [
        ("【zz_uniq_a】", "full-width bracket"),
        ("[zz_uniq_a]", "half-width bracket"),
        ("(zz_uniq_a)", "paren"),
        ("（zz_uniq_a）", "full-width paren"),
        ("`zz_uniq_a`", "backtick"),
        ("來源：zz_uniq_a", "source-colon"),
        # _tool 變體（handler 函式名）
        ("[zz_uniq_a_tool]", "_tool suffix variant"),
    ]
    for text, desc in cases:
        cleaned = claw_loop._strip_tool_name_leaks(text)
        # zz_uniq_a 與 zz_uniq_a_tool 都該被清掉
        cleaned_check = cleaned.replace("即時工具查詢", "")
        assert "zz_uniq_a" not in cleaned_check, (
            f"G6: 沒清掉 {desc}: {text!r} → {cleaned!r}"
        )

    reset_for_test()


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_cross_market_routing_in_all_languages(cryptomind_prompt_per_lang, lang):
    """A2：cross_market_routing 區塊在 4 語 prompt 都該出現。"""
    prompt = cryptomind_prompt_per_lang[lang]
    markers = {
        "zh-TW": "跨市場路由提示",
        "zh-CN": "跨市场路由提示",
        "en": "Cross-Market Routing Hints",
        "ru": "маршрутизации между рынками",
    }
    assert markers[lang] in prompt, (
        f"{lang} prompt 缺 cross_market_routing 標題（{markers[lang]!r}）。"
        "請檢查 shared.yaml 是否有補齊該 key 的 4 語。"
    )


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_cross_market_routing_gold_to_commodity(cryptomind_prompt_per_lang, lang):
    """A2：S3 soft scenario 根因解 — 黃金→get_commodity_price 路由提示存在。"""
    prompt = cryptomind_prompt_per_lang[lang]
    # 4 語都該同時提到 黃金/gold 與 get_commodity_price
    has_gold_keyword = any(kw in prompt for kw in ("黃金", "黄金", "Gold", "золот"))
    assert has_gold_keyword, f"{lang} prompt 沒提到黃金/gold 關鍵字"
    assert "get_commodity_price" in prompt, (
        f"{lang} prompt 沒把黃金/gold 連到 get_commodity_price（S3 根因未解）"
    )


# ============================================================================
# B. Token 上限 + 截斷偵測
# ============================================================================


def test_chat_max_output_tokens_is_8192():
    """B1：CHAT_MAX_OUTPUT_TOKENS 應為 8192（從 4096 放寬）。"""
    from utils.user_client_factory import CHAT_MAX_OUTPUT_TOKENS

    assert CHAT_MAX_OUTPUT_TOKENS == 8192, (
        f"CHAT_MAX_OUTPUT_TOKENS 應為 8192，實際 {CHAT_MAX_OUTPUT_TOKENS}。"
        "若改回 4096 長分析會被截斷。"
    )


def test_fallback_max_output_tokens_smaller():
    """B3：fallback 用較小 max_tokens（CHAT_MAX_OUTPUT_TOKENS_FALLBACK）。"""
    from utils.user_client_factory import (
        CHAT_MAX_OUTPUT_TOKENS,
        CHAT_MAX_OUTPUT_TOKENS_FALLBACK,
    )

    assert CHAT_MAX_OUTPUT_TOKENS_FALLBACK < CHAT_MAX_OUTPUT_TOKENS, (
        "fallback max_tokens 應小於主 max_tokens，保留預扣緩衝。"
    )
    assert CHAT_MAX_OUTPUT_TOKENS_FALLBACK == 6144


def test_truncation_detection_length_adds_notice():
    """B2：finish_reason='length' 時，_detect_and_annotate_truncation 加上告知。"""
    from core.agents.manager.claw_loop import _detect_and_annotate_truncation

    response = "這是完整回應"
    # 非 length → 不變
    out_ok = _detect_and_annotate_truncation(
        response, {"finish_reason": "stop"}, "zh-TW"
    )
    assert out_ok == response

    # length → 加告知
    out_trunc = _detect_and_annotate_truncation(
        response, {"finish_reason": "length"}, "zh-TW"
    )
    assert out_trunc != response
    assert "截斷" in out_trunc

    # 4 語都有截斷告知
    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        out = _detect_and_annotate_truncation(
            response, {"finish_reason": "length"}, lang
        )
        assert out != response, f"{lang} 沒產生截斷告知"


def test_truncation_detection_empty_response_passthrough():
    """B2：空回應不該加告知（交給 _no_valid_result_message）。"""
    from core.agents.manager.claw_loop import _detect_and_annotate_truncation

    out = _detect_and_annotate_truncation("", {"finish_reason": "length"}, "zh-TW")
    assert out == ""


def test_truncation_detection_no_finish_reason_passthrough():
    """B2：沒有 finish_reason 不該加告知（旧相容性）。"""
    from core.agents.manager.claw_loop import _detect_and_annotate_truncation

    response = "回應"
    out = _detect_and_annotate_truncation(response, {}, "zh-TW")
    assert out == response
    # None 也算
    out2 = _detect_and_annotate_truncation(
        response, {"finish_reason": None}, "zh-TW"
    )
    assert out2 == response


# ============================================================================
# C. BYOK Fallback 強化
# ============================================================================


def test_fallback_disabled_by_default(monkeypatch):
    """C1：預設情況 fallback 不該啟用。"""
    from core.agents.fallback import is_fallback_enabled

    # 清掉所有 fallback env
    for env in (
        "BYOK_FALLBACK_ENABLED",
        "BYOK_FALLBACK_PROVIDER",
        "BYOK_FALLBACK_MODEL",
        "BYOK_FALLBACK_API_KEY",
        "TEST_MODE",
    ):
        monkeypatch.delenv(env, raising=False)

    assert is_fallback_enabled() is False, (
        "fallback 必須預設關閉（Hermes #12770 cascade 反模式）。"
    )


def test_fallback_timeout_constant_is_30():
    """C2：FALLBACK_TIMEOUT_SECONDS 應為 30（Hermes #12770 教訓）。"""
    from core.agents.fallback import FALLBACK_TIMEOUT_SECONDS

    assert FALLBACK_TIMEOUT_SECONDS == 30


def test_fallback_enabled_requires_full_env(monkeypatch):
    """C1：啟用 fallback 需完整 env（避免部分設定的 false-positive）。"""
    from core.agents.fallback import is_fallback_enabled

    monkeypatch.delenv("TEST_MODE", raising=False)
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")

    assert is_fallback_enabled() is True

    # 缺 API_KEY → 不啟用
    monkeypatch.delenv("BYOK_FALLBACK_API_KEY")
    assert is_fallback_enabled() is False


def test_fallback_disabled_in_test_mode(monkeypatch):
    """C1：TEST_MODE=true 時永遠不啟用 fallback（防測試意外打 server）。"""
    from core.agents.fallback import is_fallback_enabled

    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")

    assert is_fallback_enabled() is False


# ============================================================================
# D. Tool name registry 動態同步
# ============================================================================


def test_get_tool_display_name_returns_human_readable_name():
    """D-new：get_tool_display_name 應把內部 tool_id 對應到 _TOOLS_SEED 的 display_name。
    用途：SSE progress 事件不洩漏原始 tool_id（如 get_crypto_price → 即時加密貨幣價格）。
    """
    from core.agents.tool_name_registry import (
        get_tool_display_name,
        invalidate_display_name_cache,
    )

    invalidate_display_name_cache()
    # get_crypto_price 是 _TOOLS_SEED 的固定 entry
    display = get_tool_display_name("get_crypto_price")
    assert display == "即時加密貨幣價格"
    # 另一個確認（web_search）
    assert get_tool_display_name("web_search") == "網路搜尋"


def test_get_tool_display_name_fallback_for_unknown_id():
    """D-new：未知的 tool_id 不回傳原 ID（避免洩漏），fallback 到中性詞「工具」。"""
    from core.agents.tool_name_registry import get_tool_display_name

    assert get_tool_display_name("nonexistent_tool_xyz") == "工具"
    # 空字串也 fallback
    assert get_tool_display_name("") == "工具"


def test_get_tool_display_name_does_not_leak_internal_id():
    """D-new（#1 迴歸守門）：回傳值絕對不能含原始 snake_case tool_id。"""
    from core.agents.tool_name_registry import get_tool_display_name

    # 已知 tool：display_name 不含底線 ID
    display = get_tool_display_name("fetch_url")
    assert "fetch_url" not in display
    # 未知 tool：fallback「工具」也不含輸入
    fb = get_tool_display_name("some_internal_tool_id")
    assert "some_internal_tool_id" not in fb


@pytest.mark.unit
def test_tool_name_registry_fallback_when_no_bootstrap():
    """D1：bootstrap 未跑時，用 fallback 清單。"""
    from core.agents.tool_name_registry import (
        get_registered_tool_names,
        is_dynamic_registry_active,
        reset_for_test,
    )

    reset_for_test()
    assert is_dynamic_registry_active() is False
    names = get_registered_tool_names()
    assert "get_crypto_price" in names
    assert "resolve_symbol" in names
    assert "get_commodity_price" in names


def test_tool_name_registry_dynamic_after_register():
    """D1：register_tool_names() 後用動態清單。"""
    from core.agents.tool_name_registry import (
        get_registered_tool_names,
        is_dynamic_registry_active,
        register_tool_names,
        reset_for_test,
    )

    reset_for_test()
    custom = {"my_custom_tool", "another_tool"}
    register_tool_names(custom)

    assert is_dynamic_registry_active() is True
    names = get_registered_tool_names()
    assert names == custom
    # 確認動態清單完全覆蓋 fallback
    assert "get_crypto_price" not in names  # fallback 清單不該出現

    reset_for_test()  # 還原


def test_tool_name_registry_reset_for_test():
    """D1：reset_for_test 清空動態清單。"""
    from core.agents.tool_name_registry import (
        is_dynamic_registry_active,
        register_tool_names,
        reset_for_test,
    )

    register_tool_names({"x"})
    assert is_dynamic_registry_active() is True
    reset_for_test()
    assert is_dynamic_registry_active() is False


def test_strip_tool_name_leaks_uses_dynamic_registry():
    """D3：_strip_tool_name_leaks 該用動態 registry（不再用 hardcode）。"""
    from core.agents.manager import claw_loop
    from core.agents.tool_name_registry import register_tool_names, reset_for_test

    reset_for_test()
    # 動態註冊一個新 tool（fallback 清單沒有的）
    register_tool_names({"my_unique_tool_xyz"})

    text = "來源：【my_unique_tool_xyz】"
    cleaned = claw_loop._strip_tool_name_leaks(text)
    # 因為動態清單生效，my_unique_tool_xyz 會被移除
    assert "my_unique_tool_xyz" not in cleaned

    reset_for_test()


def test_strip_tool_name_leaks_fallback_when_no_bootstrap():
    """D3：bootstrap 未跑時，fallback 清單仍能清基本 tool name。"""
    from core.agents.manager import claw_loop
    from core.agents.tool_name_registry import reset_for_test

    reset_for_test()
    text = "來源：get_crypto_price"
    cleaned = claw_loop._strip_tool_name_leaks(text)
    # fallback 清單含 get_crypto_price，仍會被替換
    assert "get_crypto_price" not in cleaned or "即時工具查詢" in cleaned


def test_strip_tool_name_leaks_covers_tool_suffix_variant():
    """D3 強化：自動覆蓋 <name>_tool 變體（LangChain @tool handler 函式名）。

    例如註冊名 us_stock_snapshot，handler 是 us_stock_snapshot_tool；
    LLM 偶爾會把 handler 名寫進回應。sanitizer 要兩個都能清。
    """
    from core.agents.manager import claw_loop
    from core.agents.tool_name_registry import register_tool_names, reset_for_test

    reset_for_test()
    # 只註冊 us_stock_snapshot（不含 _tool 後綴）
    register_tool_names({"us_stock_snapshot"})

    # LLM 寫出 handler 函式名（us_stock_snapshot_tool）
    text = "資料來源：us_stock_snapshot_tool"
    cleaned = claw_loop._strip_tool_name_leaks(text)
    assert "us_stock_snapshot_tool" not in cleaned, (
        f"_tool 後綴變體沒被清掉：{cleaned}"
    )

    reset_for_test()


def test_strip_tool_name_leaks_preserves_normal_text_with_tool_word():
    """D3：正常的英文單字 'tool' 不該被誤刪。"""
    from core.agents.manager import claw_loop
    from core.agents.tool_name_registry import reset_for_test

    reset_for_test()
    # 「use the right tool」是正常英文，不該被動到
    text = "Choose the right tool for the job."
    cleaned = claw_loop._strip_tool_name_leaks(text)
    # 原句應保留（'tool' 不是任何註冊 tool name）
    assert "right tool" in cleaned


# ============================================================================
# E. <unk> garbage cleanup（nemotron 崩壞防護）
# ============================================================================


def test_strip_unk_garbage_no_unk_passthrough():
    """E：沒 <unk> 的文字不變。"""
    from core.agents.manager.claw_loop import _strip_unk_garbage

    text = "比特幣現價 64707 美元"
    assert _strip_unk_garbage(text) == text


def test_strip_unk_garbage_dominant_returns_empty():
    """E：超過 50% 是 <unk> 的視為崩壞，回空字串。

    這是 nemotron free tier 偶發 bug：輸出數萬個 <unk>（無法 decode 的 token）。
    清為空字串後，claw_loop 會走既有 empty-response recovery / fallback。
    """
    from core.agents.manager.claw_loop import _strip_unk_garbage

    # 模擬 S7 turn 1 失敗 case：33799 chars 大多是 <unk>
    text = "We need to answer user" + "<unk>" * 1000
    result = _strip_unk_garbage(text)
    assert result == "", f"應該回空字串，實際：{result[:50]}..."


def test_strip_unk_garbage_partial_removes_only_runs():
    """E：局部 <unk> 只清連續區塊，保留其他文字。"""
    from core.agents.manager.claw_loop import _strip_unk_garbage

    # 用一個夠長的正常字串（讓 <unk> 佔比 < 50%）
    text = (
        "比特幣（BTC）目前價格約 64707 美元，過去 24 小時上漲 1.15%。"
        "技術指標 RSI 約 53.9，MACD 為正值顯示多頭動能仍在。"
        "<unk><unk><unk>"
        "建議關注 64703 美元阻力位能否有效突破。"
    )
    result = _strip_unk_garbage(text)
    assert "比特幣" in result
    assert "建議關注" in result
    assert "<unk>" not in result


def test_clean_claw_response_strips_dominant_unk():
    """E：完整 _clean_claw_response 流程也要處理 <unk> 崩壞。"""
    from core.agents.manager.claw_loop import _clean_claw_response

    text = "<unk>" * 500
    cleaned = _clean_claw_response(text)
    assert cleaned == "", f"預期空字串，實際：{cleaned[:50]}"


# ============================================================================
# E2: Nemotron loop hallucination 崩壞防護
# （S1 第二輪觀察到：「Also there is 'us-stock-technical'. ...」無限重複）
# ============================================================================


def test_repetition_breakdown_detected():
    """E2：nemotron loop hallucination 偵測 — phrase 重複 10+ 次視為崩壞。"""
    from core.agents.manager.claw_loop import _check_repetition_breakdown

    # 模擬 S1 第二輪的真實失敗 case
    text = (
        "We need to answer the user query. "
        "Also there is 'us-stock-technical' skill. " * 30
        + "Should call this skill."
    )
    result = _check_repetition_breakdown(text)
    assert result == "", f"應偵測為崩壞回空字串，實際 len={len(result)}"


def test_repetition_breakdown_normal_text_passthrough():
    """E2：正常金融分析文字（含表格、條列）不該被誤判。"""
    from core.agents.manager.claw_loop import _check_repetition_breakdown

    text = (
        "比特幣（BTC）目前價格約 64707 美元，24 小時上漲 1.15%。"
        "技術指標：RSI 53.9（中性），MACD 正值顯示多頭動能仍在。"
        "MA7 = 64255，MA25 = 62497，短期均線在長期之上，多頭排列。"
        "阻力位 64703 美元，支撐位 59882 美元。"
        "市場情緒：恐懼貪婪指數 28（恐懼區間）。"
        "建議：短線觀察阻力突破；中長線待 MACD 金叉訊號。"
    )
    result = _check_repetition_breakdown(text)
    assert result == text, "正常分析文字不該被誤判為崩壞"


def test_repetition_breakdown_short_text_passthrough():
    """E2：短文字（< 200 字）直接放行，不做重複檢測。"""
    from core.agents.manager.claw_loop import _check_repetition_breakdown

    text = "好的" * 50  # 100 字
    result = _check_repetition_breakdown(text)
    assert result == text


# ============================================================================
# E3: Nemotron token salad 崩壞防護
# （S1 第 5 輪 smoke test 觀察到：「whetherurp Wys pagomistenness...」）
# ============================================================================


def test_gibberish_breakdown_detected_real_sample():
    """E3：nemotron token salad 偵測（用真實崩壞樣本驗證）。"""
    from core.agents.manager.claw_loop import _check_gibberish_breakdown

    # 真實崩壞 case（截取自 S1 第 5 輪 smoke）
    gibberish = (
        "We need to answer whetherurp/comp Wys pagomistenness[position Stock BXffeurp"
        "DowDowfwennessLV.blade depress Bpvra BpinibEmployeeDX ERP lanc Lumpawatslash"
        "urpurp EmployeeDow urbLogotransparentzggifMgrurpFrontMaskTruthurp Employee BX "
        "ExprTT Employee Bpurpwebkit TT PWurp pushing(Playerurpurpträ HREFwebkiturpineki"
    ) * 20  # 加長到 > 500 字元
    assert len(gibberish) > 500
    result = _check_gibberish_breakdown(gibberish)
    assert result == "", f"應偵測為亂碼崩壞回空字串，實際 len={len(result)}"


def test_gibberish_breakdown_normal_english_passthrough():
    """E3：正常英文金融分析不該被誤判。"""
    from core.agents.manager.claw_loop import _check_gibberish_breakdown

    text = (
        "Based on the latest 7-day volatility data, ETH is more volatile than BTC. "
        "ETH shows a higher 7-day volatility (2.56% vs 1.91%), meaning its price "
        "tends to swing more sharply over a week than Bitcoin's. Higher volatility "
        "implies higher short-term risk (and potentially higher short-term reward). "
        "Suggested allocation for a $10,000 portfolio: Conservative (70% BTC / 30% ETH), "
        "Moderate (60% BTC / 40% ETH), Aggressive (50% BTC / 50% ETH). "
        "This balances the lower volatility of BTC with the higher growth potential of ETH."
    )
    assert len(text) > 500
    result = _check_gibberish_breakdown(text)
    assert result == text, "正常英文分析被誤判為亂碼"


def test_gibberish_breakdown_normal_chinese_passthrough():
    """E3：中文金融分析不該被誤判（全是 non-ASCII word）。"""
    from core.agents.manager.claw_loop import _check_gibberish_breakdown

    text = (
        "根據目前的即時行情與技術指標，比特幣（BTC）目前處於中性偏多但接近短期阻力位。"
        "技術面：RSI 約 53.9，處於中性區間。MACD 為正值（239.6），顯示多頭動能仍在。"
        "MA7 約 64,255 美元，MA25 約 62,497 美元，短期均線在長期均線之上，呈多頭排列。"
        "阻力位約 64,703 美元，支撐位約 59,882 美元。建議：保守（70% BTC / 30% ETH），"
        "穩健（60% BTC / 40% ETH），積極（50% BTC / 50% ETH）。"
    ) * 3  # 加長到 > 500
    assert len(text) > 500
    result = _check_gibberish_breakdown(text)
    assert result == text, "中文分析被誤判"


def test_gibberish_breakdown_short_text_passthrough():
    """E3：短文字（< 500 字）不檢測。"""
    from core.agents.manager.claw_loop import _check_gibberish_breakdown

    # 即使是亂碼，< 500 字也不檢測
    text = "whetherurp Wys pagomistenness BXffeurp" * 5  # ~200 字
    result = _check_gibberish_breakdown(text)
    assert result == text


def test_gibberish_breakdown_markdown_table_passthrough():
    """E3：markdown 表格不該被誤判（即使有長 row）。"""
    from core.agents.manager.claw_loop import _check_gibberish_breakdown

    text = (
        "| Asset | Current price (USD) | 7-day volatility | RSI (14) | "
        "MACD histogram | 20-day MA | 50-day MA | 52w high | 52w low |\n"
        "|-------|--------------------|------------------|----------|"
        "-----------------|------------|------------|----------|---------|\n"
        "| BTC   | $64,599.80         | 1.91%            | 53.9     | "
        "+239.6          | $64,255    | $62,497    | $69,000  | $49,200 |\n"
        "| ETH   | $1,873.80          | 2.56%            | 48.2     | "
        "+15.4           | $1,890     | $1,820     | $2,030   | $1,420  |\n"
    ) * 8  # 加長到 > 500
    assert len(text) > 500
    result = _check_gibberish_breakdown(text)
    assert result == text, "markdown 表格被誤判"


def test_strip_unk_garbage_cascades_to_e3_gibberish():
    """E3：_strip_unk_garbage 完整流程要能抓 gibberish（透過 cascade）。"""
    from core.agents.manager.claw_loop import _strip_unk_garbage

    gibberish = (
        "whetherurp Wys pagomistenness[position BXffeurpDowDowfwennessLV "
        "EmployeeDX lumpslashurpurp Bpurpwebkit"
    ) * 30
    result = _strip_unk_garbage(gibberish)
    assert result == "", "E3 gibberish 沒被 _strip_unk_garbage cascade 抓到"
