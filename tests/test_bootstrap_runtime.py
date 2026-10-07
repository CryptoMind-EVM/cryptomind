from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from core.agents.bootstrap import (
    LanguageAwareLLM,
    bootstrap,
    get_manager_instance,
    invalidate_manager_cache,
)


def make_mock_llm(content="ok"):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=content)
    llm.ainvoke = MagicMock(return_value=MagicMock(content=content))
    return llm


def test_bootstrap_isolates_managers_by_session():
    user_id = "bootstrap-test-user"
    invalidate_manager_cache(user_id)

    manager_a = bootstrap(
        make_mock_llm("a"),
        web_mode=False,
        user_id=user_id,
        session_id="session-a",
    )
    manager_a_reused = bootstrap(
        make_mock_llm("a2"),
        web_mode=False,
        user_id=user_id,
        session_id="session-a",
    )
    manager_b = bootstrap(
        make_mock_llm("b"),
        web_mode=False,
        user_id=user_id,
        session_id="session-b",
    )

    assert manager_a_reused is manager_a
    assert manager_a is not manager_b
    assert get_manager_instance(user_id, "session-a") is manager_a
    assert get_manager_instance(user_id, "session-b") is manager_b

    invalidate_manager_cache(user_id)


@pytest.mark.asyncio
async def test_language_aware_llm_ainvoke_injects_system_message():
    class DummyLLM:
        def __init__(self):
            self.last_messages = None

        async def ainvoke(self, messages, **kwargs):
            self.last_messages = list(messages)
            return MagicMock(content="done")

    dummy = DummyLLM()
    wrapped = LanguageAwareLLM(dummy, language="zh-TW")

    response = await wrapped.ainvoke([HumanMessage(content="測試訊息")])

    assert response.content == "done"
    assert isinstance(dummy.last_messages[0], SystemMessage)
    assert "請以繁體中文回覆所有回應" in dummy.last_messages[0].content
    assert dummy.last_messages[1].content == "測試訊息"


# ============================================================================
# LanguageAwareLLM._INSTRUCTIONS — 4 個官方支援語言都有專屬指示
# 修復背景：_SUPPORTED_LANGUAGES = {"zh-TW", "zh-CN", "en", "ru"}，但
# _INSTRUCTIONS 只放了 zh-TW / en。zh-CN 被 fallback 成繁中指示，簡體用戶
# 拿到「請以繁體中文回覆」→ 回簡中問題的時候 LLM 用繁中答。
# ============================================================================


def test_language_aware_llm_has_instruction_for_all_supported_languages():
    """api/routers/user.py:_SUPPORTED_LANGUAGES 公開支援的語言，
    _INSTRUCTIONS 必須全部涵蓋，避免 fallback 到非預設語言。"""
    supported = {"zh-TW", "zh-CN", "en", "ru"}
    missing = supported - set(LanguageAwareLLM._INSTRUCTIONS.keys())
    assert not missing, f"_INSTRUCTIONS 缺少: {missing}"


def test_language_aware_llm_zh_cn_uses_simplified_chinese_instruction():
    """簡體中文用戶應收到『简体』指示，不該 fallback 到繁體。"""
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="zh-CN")
    assert "简体" in llm._lang_msg
    assert "繁體" not in llm._lang_msg


def test_language_aware_llm_zh_tw_uses_traditional_chinese_instruction():
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="zh-TW")
    assert "繁體" in llm._lang_msg
    assert "简体" not in llm._lang_msg


def test_language_aware_llm_en_uses_english_instruction():
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="en")
    assert "English ONLY" in llm._lang_msg


def test_language_aware_llm_ru_uses_russian_instruction():
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="ru")
    assert "РУССКОМ" in llm._lang_msg


def test_language_aware_llm_unknown_zh_variant_falls_back_to_zh_tw():
    """未知中文變體（zh-HK / zh-SG 等）→ 預設繁中（產品主市場）。"""
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="zh-HK")
    assert "繁體" in llm._lang_msg


def test_language_aware_llm_completely_unknown_language_falls_back_to_zh_tw():
    """完全未知語言 → 仍預設繁中，不爆。"""
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language="klingon")
    assert "繁體" in llm._lang_msg


def test_language_aware_llm_none_language_falls_back_to_zh_tw():
    dummy = MagicMock()
    llm = LanguageAwareLLM(dummy, language=None)
    assert "繁體" in llm._lang_msg
