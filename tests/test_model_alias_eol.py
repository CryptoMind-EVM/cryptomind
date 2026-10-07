"""下架模型別名（2026-09-12）：NVIDIA 的 minimax-m3 於 09-09 下架，所有 NVIDIA 使用者每題 410。

守：預設模型不再是下架的那個；使用者存過的舊名在 client factory 這層改走替代模型。
"""

from __future__ import annotations

import inspect

import pytest

from core.model_config import get_default_model, resolve_model_alias

pytestmark = pytest.mark.unit


def test_nvidia_default_is_not_the_retired_model():
    assert get_default_model("nvidia") != "minimaxai/minimax-m3"
    assert get_default_model("nvidia") == "deepseek-ai/deepseek-v4-flash-0731"


def test_alias_maps_retired_model_and_passes_others_through():
    assert (
        resolve_model_alias("nvidia", "minimaxai/minimax-m3")
        == "deepseek-ai/deepseek-v4-flash-0731"
    )
    assert (
        resolve_model_alias("nvidia", "moonshotai/kimi-k2.6") == "moonshotai/kimi-k2.6"
    )
    assert (
        resolve_model_alias("openai", "minimaxai/minimax-m3") == "minimaxai/minimax-m3"
    )
    assert resolve_model_alias("nvidia", None) is None


def test_client_factory_resolves_alias_before_building_client():
    from utils import user_client_factory as f

    src = inspect.getsource(f.create_user_llm_client)
    assert "resolve_model_alias(provider, requested_model)" in src
    assert src.index("resolve_model_alias(") < src.index('"model": resolved_model')
