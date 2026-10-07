"""早報「一句話」的護欄：數字要有來源、不准買賣指令、失敗就省略。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.daily_brief.insight import generate_insight, sanitize_insight

pytestmark = pytest.mark.unit

BRIEF = "📊 你的持倉\n  2330 +1.8%\n  BTC -2.1%\n💸 昨天花費 1,240 TWD"


class TestSanitize:
    def test_keeps_sentence_whose_numbers_come_from_brief(self):
        out = sanitize_insight("台積電 +1.8% 但 BTC 跌 2.1%，要看支撐嗎？", BRIEF)
        assert out == "台積電 +1.8% 但 BTC 跌 2.1%，要看支撐嗎？"

    def test_drops_numbers_without_source(self):
        assert sanitize_insight("BTC 可能回到 58,000，要不要進場？", BRIEF) is None

    def test_drops_buy_sell_instructions(self):
        assert sanitize_insight("建議全倉買進 BTC", BRIEF) is None
        assert sanitize_insight("You should buy now before it moves", BRIEF) is None

    def test_strips_quotes_whitespace_and_caps_length(self):
        assert (
            sanitize_insight("  「今天沒什麼事，  要看哪一檔？」 ", BRIEF)
            == "今天沒什麼事， 要看哪一檔？"
        )
        long = "看" * 300 + "？"
        out = sanitize_insight(long, BRIEF)
        assert out and len(out) <= 140 and out.endswith("…")

    def test_empty_is_none(self):
        assert sanitize_insight("", BRIEF) is None
        assert sanitize_insight(None, BRIEF) is None


LOCAL = {
    "provider": "local_llama",
    "api_key": "not-needed",
    "model": "neohorse-1-9b",
    "local": True,
    "fallback": True,
}


class TestGenerate:
    async def test_no_local_model_means_no_insight(self):
        with patch("core.agents.fallback.fallback_credentials", return_value=None):
            assert await generate_insight("u1", "free", "zh-TW", BRIEF) is None

    async def test_cloud_fallback_is_never_used(self):
        """鏈上就算設了雲端備援，本地掛掉時也不用（早報不花雲端 token）。"""
        cloud = {**LOCAL, "provider": "deepseek", "local": False}
        with (
            patch("core.agents.fallback.fallback_credentials", return_value=cloud),
            patch("utils.user_client_factory.create_user_llm_client") as create,
        ):
            assert await generate_insight("u1", "premium", "zh-TW", BRIEF) is None
        create.assert_not_called()

    async def test_user_byok_key_is_not_used(self):
        """使用者綁了自己的金鑰也不用：只建本地模型的 client。"""
        class _Resp:
            content = "台積電 +1.8% 領漲，要不要看法人動向？"

        fake_client = AsyncMock()
        fake_client.ainvoke = AsyncMock(return_value=_Resp())
        with (
            patch(
                "api.user_llm.resolve_user_llm_credentials",
                new=AsyncMock(
                    return_value={"provider": "deepseek", "api_key": "k", "model": "m"}
                ),
            ) as byok,
            patch("core.agents.fallback.fallback_credentials", return_value=LOCAL),
            patch(
                "utils.user_client_factory.create_user_llm_client",
                return_value=fake_client,
            ) as create,
        ):
            assert await generate_insight("u1", "premium", "zh-TW", BRIEF)
        byok.assert_not_called()
        assert create.call_args.kwargs["provider"] == "local_llama"

    async def test_llm_failure_is_swallowed(self):
        with (
            patch("core.agents.fallback.fallback_credentials", return_value=LOCAL),
            patch(
                "utils.user_client_factory.create_user_llm_client",
                side_effect=RuntimeError("boom"),
            ),
        ):
            assert await generate_insight("u1", "free", "zh-TW", BRIEF) is None

    async def test_llm_reply_goes_through_sanitizer(self):
        class _Resp:
            content = "台積電 +1.8% 領漲，要不要看法人動向？"

        fake_client = AsyncMock()
        fake_client.ainvoke = AsyncMock(return_value=_Resp())
        with (
            patch("core.agents.fallback.fallback_credentials", return_value=LOCAL),
            patch(
                "utils.user_client_factory.create_user_llm_client",
                return_value=fake_client,
            ),
        ):
            out = await generate_insight("u1", "premium", "zh-TW", BRIEF)
            assert out == "台積電 +1.8% 領漲，要不要看法人動向？"
            # 模型偷加數字 → 整句丟掉
            _Resp.content = "BTC 目標 70,000，要不要看？"
            assert await generate_insight("u1", "premium", "zh-TW", BRIEF) is None
