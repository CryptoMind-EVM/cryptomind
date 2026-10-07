"""Prompt 分層第一步（2026-09-12）：旗標關著的功能不該佔 system prompt。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agents.agents import cryptomind_agent as cm
from core.agents.prompt_registry import PromptRegistry

pytestmark = pytest.mark.unit


class _Registry:
    def list_for_agent(self, _name):
        return []


def _agent():
    return cm.CryptoMindAgent(llm_client=SimpleNamespace(), tool_registry=_Registry())


def _self_manage_marker(lang: str) -> str:
    PromptRegistry.load()
    text = PromptRegistry.get("shared", "self_manage_skills", lang) or ""
    assert text, "shared.yaml 少了 self_manage_skills"
    return text.strip().splitlines()[0][:40]


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_self_manage_section_absent_when_flag_off(monkeypatch, lang):
    monkeypatch.setattr(cm, "AGENT_SELF_MANAGE_ENABLED", False)
    prompt = _agent()._get_system_prompt(lang)
    assert _self_manage_marker(lang) not in prompt


def test_self_manage_section_present_when_flag_on(monkeypatch):
    monkeypatch.setattr(cm, "AGENT_SELF_MANAGE_ENABLED", True)
    prompt = _agent()._get_system_prompt("en")
    assert _self_manage_marker("en") in prompt
