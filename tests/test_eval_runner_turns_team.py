"""eval 執行器：多輪（turns）與多 agent preset（team）的接線（零 LLM）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "eval_claw", REPO / "scripts" / "eval_claw.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_claw"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_history_format_matches_analysis_router():
    mod = _load_runner()
    text = mod._history_from_turns(
        [("比特幣現在多少？", "現價 61,234。"), ("那它的 RSI 呢？", "")]
    )
    assert text.splitlines() == [
        "用戶: 比特幣現在多少？",
        "助手: 現價 61,234。",
        "用戶: 那它的 RSI 呢？",
    ]
    assert mod._history_from_turns([]) == ""


def test_preset_config_resolves_profiles():
    mod = _load_runner()
    cfg = mod._preset_config_for(
        {"preset": {"agent_ids": ["finance_markets", "onchain_security"]}}
    )
    assert cfg["agent_ids"] == ["finance_markets", "onchain_security"]
    assert cfg["user_tier"] == "premium" and cfg["tool_names"], "工具池要解析出來"
    assert mod._preset_config_for({"query": "x"}) is None


def test_golden_has_50_plus_cases_with_new_kinds():
    rows = [
        json.loads(line)
        for line in (REPO / "evals" / "claw_golden.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(rows) >= 50
    cats = {r["category"] for r in rows}
    assert {"multiturn", "team"} <= cats
    for r in rows:
        if r["category"] == "multiturn":
            assert len(r["turns"]) >= 2 and r["query"] == r["turns"][-1]
        if r["category"] == "team":
            assert (
                len(r["preset"]["agent_ids"]) >= 2
                and r["expect"]["route"] == "claw_loop"
            )
    # 市場覆蓋：台股、港日韓 A 股、外匯、商品、鏈上都要有題
    families = {f for r in rows for f in r.get("expect", {}).get("tool_families", [])}
    assert {"tw_stock", "global_stock", "forex", "commodity", "onchain"} <= families
    assert len({r["id"] for r in rows}) == len(rows), "id 不得重複"


def test_stub_covers_every_case_including_new_kinds():
    mod = _load_runner()
    cases = mod.load_golden()
    runs = {c["id"]: mod.run_case_stub(c) for c in cases}
    scores = mod._score_all(cases, runs)
    failed = {k: v.failures for k, v in scores.items() if not v.passed}
    assert not failed, failed


async def test_capture_accumulates_tools_across_node_calls():
    """一題可能呼叫 execute_streaming 多次（nudge／clarify 重跑），工具要累加不是只留最後一次。"""
    mod = _load_runner()

    class _Agent:
        async def execute_streaming(self, task, *a, **k):
            from types import SimpleNamespace

            return SimpleNamespace(
                message="ok",
                data={"used_tools": [task], "tool_outputs": [f"{task}-out"]},
            )

    agent = _Agent()
    manager = type(
        "M", (), {"agent_registry": type("R", (), {"get": lambda self, n: agent})()}
    )()
    sink = {}
    mod._wrap_agent_capture(manager, sink)
    await agent.execute_streaming("t1")
    await agent.execute_streaming("t2")
    assert sink["used_tools"] == ("t1", "t2") and sink["tool_outputs"] == (
        "t1-out",
        "t2-out",
    )
