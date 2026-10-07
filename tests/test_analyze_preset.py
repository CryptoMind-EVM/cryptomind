"""Analyze preset 整合測試 — api/routers/analysis.py（impl plan Task B5）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.analysis import _resolve_agent_preset_config

PREMIUM_USER = {"user_id": "u1", "username": "t", "membership_tier": "premium"}
FREE_USER = {"user_id": "u2", "username": "f", "membership_tier": "free"}


def _preset(**overrides):
    data = {
        "preset_id": "prst_1",
        "user_id": "u1",
        "name": "PP",
        "mode": "single",
        "agent_ids": ["people_projects"],
        "analysis_mode": "quick",
        "action_policy": "read_only",
        "capability_overrides": {},
        "is_default": True,
        "config_version": "2026.08.0",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _repo_patched(get_preset=None, get_default=None):
    repo = SimpleNamespace(
        get_preset=AsyncMock(return_value=get_preset),
        get_default_preset=AsyncMock(return_value=get_default),
    )
    return patch("core.orm.agent_presets_repo.agent_presets_repo", repo), repo


@pytest.mark.asyncio
async def test_flag_off_returns_none():
    with patch(
        "core.feature_flags.agent_presets_enabled", return_value=False
    ):
        config = await _resolve_agent_preset_config(None, PREMIUM_USER, "prst_1", None)
    assert config is None


@pytest.mark.asyncio
async def test_owned_preset_resolves():
    p, repo = _repo_patched(get_preset=_preset(), get_default=None)
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), p:
        config = await _resolve_agent_preset_config(None, PREMIUM_USER, "prst_1", None)
    assert config is not None
    repo.get_preset.assert_awaited_once()
    assert "search_projects" in config["tool_names"]
    assert "get_user_balances" not in config["tool_names"]
    assert config["preset_id"] == "prst_1"


@pytest.mark.asyncio
async def test_foreign_preset_falls_back_to_default():
    """preset_id 指向他人 preset → get_preset 查不到 → 改用 default。"""
    p, repo = _repo_patched(get_preset=None, get_default=_preset(agent_ids=["general_research"]))
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), p:
        config = await _resolve_agent_preset_config(None, PREMIUM_USER, "prst_other", None)
    repo.get_preset.assert_awaited_once()
    repo.get_default_preset.assert_awaited_once()
    assert config is not None
    assert config["profiles"] == ["general_research"]


@pytest.mark.asyncio
async def test_no_preset_at_all_returns_none():
    p, _ = _repo_patched(get_preset=None, get_default=None)
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), p:
        config = await _resolve_agent_preset_config(None, FREE_USER, None, None)
    assert config is None


@pytest.mark.asyncio
async def test_exception_degrades_to_none():
    repo = SimpleNamespace(
        get_preset=AsyncMock(side_effect=RuntimeError("db down")),
        get_default_preset=AsyncMock(side_effect=RuntimeError("db down")),
    )
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), patch(
        "core.orm.agent_presets_repo.agent_presets_repo", repo
    ):
        config = await _resolve_agent_preset_config(None, FREE_USER, "prst_1", None)
    assert config is None


@pytest.mark.asyncio
async def test_client_enabled_tools_intersected():
    """client enabled_tools 只能縮小 preset 工具池。"""
    p, _ = _repo_patched(get_preset=_preset(), get_default=None)
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), p:
        config = await _resolve_agent_preset_config(
            None, PREMIUM_USER, "prst_1", ["get_project", "submit_trade"]
        )
    # submit_trade 不在 people_projects 池內 → 被剔除；只留 get_project
    assert config["tool_names"] == ["get_project"]


@pytest.mark.asyncio
async def test_free_user_premium_tools_excluded():
    p, _ = _repo_patched(
        get_preset=_preset(agent_ids=["finance_markets"]), get_default=None
    )
    with patch("core.feature_flags.agent_presets_enabled", return_value=True), p:
        config = await _resolve_agent_preset_config(None, FREE_USER, "prst_1", None)
    assert config is not None
    assert all("premium" not in t for t in config["tool_names"])
