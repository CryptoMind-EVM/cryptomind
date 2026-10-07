"""Skill 使用量指標（2026-09-12）：load_skill 命中要進 RunMetrics 的 skills= 欄位。"""

from __future__ import annotations

import logging

import pytest

from core.agents import skill_metrics
from core.agents.manager.claw_loop import log_run_metrics

pytestmark = pytest.mark.unit


def test_record_and_drain_are_per_run():
    skill_metrics.reset_loaded_skills()
    skill_metrics.record_skill_load("comparison-analysis")
    skill_metrics.record_skill_load("comparison-analysis")
    skill_metrics.record_skill_load("technical-analysis")
    assert skill_metrics.drain_loaded_skills() == [
        "comparison-analysis",
        "technical-analysis",
    ]
    assert skill_metrics.drain_loaded_skills() == []
    assert skill_metrics.snapshot()["comparison-analysis"] >= 2


def test_run_metrics_line_carries_skills(caplog):
    with caplog.at_level(logging.INFO):
        log_run_metrics(route="claw_loop", elapsed_s=1.0, skills="comparison-analysis")
        log_run_metrics(route="fast_path", elapsed_s=0.2)
    # 只看 RunMetrics 行：之後的 run_metrics_store 寫入路徑可能另有 log
    lines = [m for m in caplog.messages if "[RunMetrics]" in m]
    assert len(lines) == 2, caplog.messages
    assert "skills=comparison-analysis" in lines[0]
    assert "skills=-" in lines[1]


def test_load_skill_records_metric():
    import core.agents.tools as tools_mod

    assert (
        "record_skill_load(skill.name)"
        in open(tools_mod.__file__, encoding="utf-8").read()
    )
