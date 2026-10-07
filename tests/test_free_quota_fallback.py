"""免費配額（平台金鑰）：沒自帶金鑰的人用 BYOK_FALLBACK_* 聊天／收早報一句話，每日上限另擋。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.agents import fallback

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestFallbackCredentials:
    def test_disabled_returns_none(self, monkeypatch):
        with patch.object(fallback, "is_fallback_enabled", return_value=False):
            assert fallback.fallback_credentials() is None

    def test_enabled_returns_platform_credentials(self, monkeypatch):
        monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "DeepSeek")
        monkeypatch.setenv("BYOK_FALLBACK_API_KEY", " sk-platform ")
        monkeypatch.setenv("BYOK_FALLBACK_MODEL", "deepseek-flash")
        with patch.object(fallback, "is_fallback_enabled", return_value=True):
            creds = fallback.fallback_credentials()
        assert creds == {
            "provider": "deepseek",
            "api_key": "sk-platform",
            "model": "deepseek-flash",
            "fallback": True,
            "local": False,  # 2026-09-22：雲端 key（花錢）→ 平台日額要套
        }

    def test_test_mode_keeps_it_off(self, monkeypatch):
        """TEST_MODE 下永遠關——測試不能意外打到平台金鑰。"""
        monkeypatch.setenv("TEST_MODE", "true")
        monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
        monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "deepseek")
        monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "k")
        assert fallback.is_fallback_enabled() is False
        assert fallback.fallback_credentials() is None

    def test_daily_limit_parsing(self, monkeypatch):
        monkeypatch.delenv("BYOK_FALLBACK_DAILY_LIMIT", raising=False)
        assert fallback.fallback_daily_limit() == fallback.DEFAULT_FALLBACK_DAILY_LIMIT
        monkeypatch.setenv("BYOK_FALLBACK_DAILY_LIMIT", "25")
        assert fallback.fallback_daily_limit() == 25
        monkeypatch.setenv("BYOK_FALLBACK_DAILY_LIMIT", "-3")
        assert fallback.fallback_daily_limit() == 0
        monkeypatch.setenv("BYOK_FALLBACK_DAILY_LIMIT", "abc")
        assert fallback.fallback_daily_limit() == fallback.DEFAULT_FALLBACK_DAILY_LIMIT


class TestWiring:
    def test_chat_path_tries_platform_key_before_400_and_caps_daily(self):
        src = (REPO / "api" / "routers" / "analysis.py").read_text(encoding="utf-8")
        assert src.count("fallback_credentials(") >= 2, "chat 與 greeting 兩條路都要接"
        assert "Free quota reached" in src and "fallback_daily_limit()" in src
        # 平台金鑰的上限只套在「花錢的」fallback 憑證上（自帶金鑰、本地零成本模型不受影響）
        assert (
            'credentials.get("fallback")' in src
            and 'not credentials.get("local")' in src
        )
        # 分層：chat 路徑把會員等級傳進去（免費→本地優先、進階→雲端優先）
        assert (
            "fallback_credentials(" in src and "tier=normalize_membership_tier(" in src
        )

    async def test_brief_insight_uses_platform_local_model(self):
        """2026-09-27（#920）：早報的一句話一律用平台本地模型，不再先用使用者自己的金鑰。"""
        from core.daily_brief.insight import generate_insight

        class _Resp:
            content = "台積電 +1.8% 領漲，要不要看法人動向？"

        fake_client = AsyncMock()
        fake_client.ainvoke = AsyncMock(return_value=_Resp())
        with (
            patch(
                "api.user_llm.resolve_user_llm_credentials",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "core.agents.fallback.fallback_credentials",
                return_value={
                    "provider": "local_llama",
                    "api_key": "not-needed",
                    "model": "neohorse-1-9b",
                    "fallback": True,
                    "local": True,
                },
            ),
            patch(
                "utils.user_client_factory.create_user_llm_client",
                return_value=fake_client,
            ) as mk,
        ):
            out = await generate_insight(
                "u1", "free", "zh-TW", "📊 你的持倉\n  2330 +1.8%"
            )
        assert out == "台積電 +1.8% 領漲，要不要看法人動向？"
        assert mk.call_args.kwargs["provider"] == "local_llama"

    def test_env_example_documents_all_five(self):
        env = (REPO / ".env.example").read_text(encoding="utf-8")
        for key in (
            "BYOK_FALLBACK_ENABLED",
            "BYOK_FALLBACK_PROVIDER",
            "BYOK_FALLBACK_MODEL",
            "BYOK_FALLBACK_API_KEY",
            "BYOK_FALLBACK_DAILY_LIMIT",
        ):
            assert f"# {key}=" in env, key
