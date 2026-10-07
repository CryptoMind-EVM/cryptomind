"""Router 推理預算旋鈕（``ROUTER_REASONING_EFFORT``）的守衛。

背景：2026-09-06 量測推翻了 ModelRouter 的設計前提。Router 那次分類呼叫，
換模型幾乎沒有效果（pro 3.13s vs flash 3.06s），有效的是關掉推理（0.74s，
4 倍）。但那是**拿準確度換速度**：eval n=131，domains 90.8% → 87.8%，
只看 router bucket 是 84.3% → 78.1%。所以預設必須是關的。
"""

from __future__ import annotations

import pytest

from core.agents.manager.llm import _reasoning_effort_for, _with_reasoning_budget


class _Bindable:
    def __init__(self):
        self.bound = None

    def bind(self, **kw):
        self.bound = kw
        return f"bound:{kw}"


class _Unbindable:
    """有些包裝層沒有 bind()——不能因此炸掉主流程。"""


class _BindExplodes:
    def bind(self, **kw):
        raise TypeError("endpoint 不吃 reasoning_effort")


# ── 預設必須是不動的 ────────────────────────────────────────────────────


def test_defaults_to_no_change(monkeypatch):
    """沒設環境變數時不能偷偷改變路由行為——這是拿準確度換速度的交換。"""
    monkeypatch.delenv("ROUTER_REASONING_EFFORT", raising=False)
    assert _reasoning_effort_for("router") == ""


def test_default_leaves_the_llm_object_untouched(monkeypatch):
    monkeypatch.delenv("ROUTER_REASONING_EFFORT", raising=False)
    llm = _Bindable()
    assert _with_reasoning_budget(llm, "router") is llm
    assert llm.bound is None


# ── 只作用在 router ─────────────────────────────────────────────────────


@pytest.mark.parametrize("task_type", ["deep_analysis", "simple_qa", "market_data", None])
def test_other_task_types_are_never_touched(task_type, monkeypatch):
    """deep_analysis 是使用者真正在讀的答案，關它的推理會直接砍品質。

    simple_qa 也不動：那條是快速通道**寫答案**，不是分類。
    """
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "none")
    assert _reasoning_effort_for(task_type) == ""
    llm = _Bindable()
    assert _with_reasoning_budget(llm, task_type) is llm


def test_router_picks_up_the_setting(monkeypatch):
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "none")
    assert _reasoning_effort_for("router") == "none"
    llm = _Bindable()
    out = _with_reasoning_budget(llm, "router")
    assert llm.bound == {"reasoning_effort": "none"}
    assert out != llm


def test_value_is_passed_through_verbatim(monkeypatch):
    """OpenAI o 系列吃 low/medium/high，不是只有 none——別把值寫死。"""
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "low")
    llm = _Bindable()
    _with_reasoning_budget(llm, "router")
    assert llm.bound == {"reasoning_effort": "low"}


def test_whitespace_only_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "   ")
    llm = _Bindable()
    assert _with_reasoning_budget(llm, "router") is llm


# ── 失敗要降級不要擋路 ──────────────────────────────────────────────────


def test_llm_without_bind_is_returned_as_is(monkeypatch):
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "none")
    llm = _Unbindable()
    assert _with_reasoning_budget(llm, "router") is llm


def test_bind_failure_falls_back_to_original(monkeypatch):
    """端點不吃這個參數時要照原樣跑——一個調速旋鈕不該讓整條路徑掛掉。"""
    monkeypatch.setenv("ROUTER_REASONING_EFFORT", "none")
    llm = _BindExplodes()
    assert _with_reasoning_budget(llm, "router") is llm


# ── 文件 ────────────────────────────────────────────────────────────────


def test_flag_is_documented_with_the_measured_tradeoff():
    """只寫「可以加速」而不寫代價，之後就會有人以為它是免費的。"""
    from pathlib import Path

    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "ROUTER_REASONING_EFFORT" in text
    assert "87.8" in text, ".env.example 要寫出關掉之後 domains 掉到多少"
