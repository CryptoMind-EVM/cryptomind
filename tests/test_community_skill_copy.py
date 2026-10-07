"""community-engagement skill 的說法要跟真實流程一致（TON 盤查 A10，2026-09-25）。

以前寫「每篇 0.1 TON」「綁 TON 錢包」「推薦 Tonkeeper」「綁定後就能升級 Premium」，
又會在 premium／升級／錢包這類問題被注入 prompt——模型會照著講錯的付款方式。
現在論壇與訂閱都是 Base 上的 USDC、錢包是 EVM。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "core/agents/skills/community-engagement/SKILL.md"


def test_skill_has_no_ton_claims():
    text = SKILL.read_text(encoding="utf-8")
    # 整字比對（button／tone 不算）
    assert not re.search(r"\bTON\b", text)
    for stale in ("tonkeeper", "ton connect"):
        assert stale not in text.lower(), stale
    assert "USDC on Base" in text
    assert "EVM" in text
    assert "does not unlock Premium" in text


def test_skill_i18n_names_have_no_ton():
    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        entry = data["skills"]["community-engagement"]
        assert "TON" not in entry["name"] + entry["description"], lang


def test_skill_still_loads_without_tools():
    from core.agents.skill_loader import get_skill_loader

    loader = get_skill_loader()
    loader._loaded = False
    loader._cache.clear()
    skill = loader.load_all().get("community-engagement")
    assert skill is not None
    assert skill.recommended_tools == []
    assert "ton connect" not in skill.auto_fire_keywords
