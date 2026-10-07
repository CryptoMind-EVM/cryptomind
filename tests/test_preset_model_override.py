"""per-agent 模型指定的執行期解析測試（Model Mixer Step 1）。

涵蓋：
- ``_resolve_preset_model_override``：單 agent preset 才套用、跨欄位缺漏
  降級、例外不阻斷聊天
- ManagerState 的四個 channel 已宣告——LangGraph 會丟棄未宣告 channel 的
  input 寫入（PR #617 的 preset_config 因此從未送達 claw_loop，本 PR 修復）；
  這個測試防止有人再刪掉宣告讓地雷復活
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.analysis import (
    _apply_model_override_credentials,
    _resolve_preset_model_override,
)

pytestmark = pytest.mark.unit

_PREMIUM = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}
_FREE = {"user_id": "u_free", "username": "free", "membership_tier": "free"}


def _preset(agent_ids, preset_id="prst_abc"):
    return SimpleNamespace(
        preset_id=preset_id,
        agent_ids=agent_ids,
        mode="single" if len(agent_ids) == 1 else "team",
    )


def _config(model_selection):
    return SimpleNamespace(agent_id="general_research", model_selection=model_selection)


class TestResolvePresetModelOverride:
    async def test_flag_off_returns_none(self):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=False
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_default_preset",
            new=AsyncMock(),
        ) as mock_get:
            result = await _resolve_preset_model_override(
                None, _PREMIUM, "prst_abc"
            )
        assert result is None
        mock_get.assert_not_awaited()

    async def test_no_preset_returns_none(self):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_default_preset",
            new=AsyncMock(return_value=None),
        ):
            result = await _resolve_preset_model_override(None, _PREMIUM, None)
        assert result is None

    async def test_multi_agent_preset_not_applied(self):
        """多 agent preset：資料照存，但執行期一次 run 只有一個 LLM，不套用。"""
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(
                return_value=_preset(["general_research", "finance_markets"])
            ),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.get_config",
            new=AsyncMock(),
        ) as mock_cfg:
            result = await _resolve_preset_model_override(
                None, _PREMIUM, "prst_abc"
            )
        assert result is None
        mock_cfg.assert_not_awaited()

    async def test_single_agent_with_model_returns_override(self):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.get_config",
            new=AsyncMock(
                return_value=_config(
                    {"provider": "google_gemini", "model": "gemini-3.5-flash"}
                )
            ),
        ):
            result = await _resolve_preset_model_override(
                None, _PREMIUM, "prst_abc"
            )
        assert result == {
            "agent_id": "general_research",
            "provider": "google_gemini",
            "model": "gemini-3.5-flash",
            "preset_id": "prst_abc",
        }

    @pytest.mark.parametrize(
        "selection",
        [None, {}, {"provider": "", "model": "x"}, {"provider": "openai", "model": " "}],
        ids=["no_config", "empty_dict", "missing_provider", "blank_model"],
    )
    async def test_incomplete_selection_degrades_to_none(self, selection):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.get_config",
            new=AsyncMock(return_value=_config(selection)),
        ):
            result = await _resolve_preset_model_override(
                None, _PREMIUM, "prst_abc"
            )
        assert result is None

    async def test_free_tier_returns_none(self):
        """寫入端是 Premium-only，讀取端也必須重驗。

        否則使用者降級後 user_agent_configs 那列還在，會繼續套用指定模型。
        """
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ) as mock_preset, patch(
            "core.orm.agent_configs_repo.agent_configs_repo.get_config",
            new=AsyncMock(
                return_value=_config({"provider": "openai", "model": "gpt-5.5"})
            ),
        ) as mock_cfg:
            result = await _resolve_preset_model_override(None, _FREE, "prst_abc")
        assert result is None
        mock_preset.assert_not_awaited()
        mock_cfg.assert_not_awaited()

    async def test_repo_exception_degrades_to_none(self):
        """指標與個人化絕不可阻斷聊天——任何例外都降級為無 override。"""
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await _resolve_preset_model_override(
                None, _PREMIUM, "prst_abc"
            )
        assert result is None


class TestApplyModelOverrideCredentials:
    """跨 provider 換 key 的守門。

    resolve_user_llm_credentials(preferred) 找不到 preferred 時不會回 None，
    而是回「其餘 provider 裡第一把有 key 的」。只判斷 truthy 會把 A 家的模型名
    配上 B 家的 key 送出去。
    """

    _OPENAI = {"provider": "openai", "api_key": "sk-openai", "model": "gpt-5.5"}
    _ANTHROPIC = {
        "provider": "anthropic",
        "api_key": "sk-ant",
        "model": "claude-4.5-sonnet",
    }
    _OVERRIDE_ANTHROPIC = {
        "agent_id": "general_research",
        "provider": "anthropic",
        "model": "claude-4.5-sonnet",
        "preset_id": "prst_abc",
    }

    async def test_no_override_is_passthrough(self):
        creds, override = await _apply_model_override_credentials(
            _PREMIUM, dict(self._OPENAI), None
        )
        assert creds == self._OPENAI
        assert override is None

    async def test_same_provider_does_not_refetch(self):
        override = {**self._OVERRIDE_ANTHROPIC, "provider": "openai"}
        with patch(
            "api.routers.analysis.resolve_user_llm_credentials", new=AsyncMock()
        ) as mock_resolve:
            creds, out = await _apply_model_override_credentials(
                _PREMIUM, dict(self._OPENAI), override
            )
        mock_resolve.assert_not_awaited()
        assert creds == self._OPENAI
        assert out == override

    async def test_cross_provider_with_key_swaps_credentials(self):
        with patch(
            "api.routers.analysis.resolve_user_llm_credentials",
            new=AsyncMock(return_value=dict(self._ANTHROPIC)),
        ):
            creds, out = await _apply_model_override_credentials(
                _PREMIUM, dict(self._OPENAI), dict(self._OVERRIDE_ANTHROPIC)
            )
        assert creds["provider"] == "anthropic"
        assert creds["api_key"] == "sk-ant"
        assert out == self._OVERRIDE_ANTHROPIC

    async def test_wrong_provider_returned_degrades_instead_of_mixing(self):
        """指定 anthropic、只有 openai key → 必須降級，不可拿 openai 的 key
        去送 claude 的模型名。"""
        with patch(
            "api.routers.analysis.resolve_user_llm_credentials",
            new=AsyncMock(return_value=dict(self._OPENAI)),
        ):
            creds, out = await _apply_model_override_credentials(
                _PREMIUM, dict(self._OPENAI), dict(self._OVERRIDE_ANTHROPIC)
            )
        assert out is None, "override 未清掉 → claude 模型名會被送到 openai"
        assert creds["provider"] == "openai"
        assert creds["api_key"] == "sk-openai"

    async def test_no_credentials_at_all_degrades(self):
        with patch(
            "api.routers.analysis.resolve_user_llm_credentials",
            new=AsyncMock(return_value=None),
        ):
            creds, out = await _apply_model_override_credentials(
                _PREMIUM, dict(self._OPENAI), dict(self._OVERRIDE_ANTHROPIC)
            )
        assert out is None
        assert creds == self._OPENAI


class TestResolveUserLlmCredentialsFallbackTrap:
    async def test_preferred_provider_without_key_returns_another_provider(self):
        """守門測試：這是上面那個 bug 的根源，行為若改了要有人注意到。"""
        from api.user_llm import resolve_user_llm_credentials

        async def _fake_get(_user_id, provider):
            if provider == "openai":
                return {"api_key": "sk-openai", "model": "gpt-5.5"}
            return None

        with patch(
            "api.user_llm.user_api_keys_repo.get_user_api_key_with_model",
            new=AsyncMock(side_effect=_fake_get),
        ):
            result = await resolve_user_llm_credentials(_PREMIUM, "anthropic")
        assert result is not None
        assert result["provider"] == "openai", (
            "若這裡改成回 None，_apply_model_override_credentials 的比對可以簡化"
        )


class TestManagerStateChannels:
    def test_preset_and_llm_channels_declared(self):
        """未宣告的 channel 會被 LangGraph 靜默丟棄（實測「wrote to unknown
        channel …, ignoring it」）——這四個必須留在 ManagerState。"""
        from core.agents.models import ManagerState

        for channel in (
            "preset_config",
            "preset_id",
            "llm_model",
            "llm_provider",
        ):
            assert channel in ManagerState.__annotations__, (
                f"ManagerState 缺少 {channel}——input 寫入會被 LangGraph 丟棄"
            )
