"""eval 執行器接線（stub 模式，零 LLM）：golden set 全跑一遍要能評分，故意加幻覺數字要被抓到。"""

from __future__ import annotations

import importlib.util
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


def test_stub_run_scores_every_golden_case():
    mod = _load_runner()
    cases = mod.load_golden()
    runs = {c["id"]: mod.run_case_stub(c) for c in cases}
    scores = mod._score_all(cases, runs)
    summary = mod.summarize(scores)
    assert summary["total"] == len(cases) >= 20
    assert summary["pass_rate"] >= 0.9, {
        k: v.failures for k, v in scores.items() if not v.passed
    }


def test_stub_bad_mode_is_caught_by_number_provenance():
    mod = _load_runner()
    cases = mod.load_golden(categories={"lookup", "hallucination"})
    runs = {c["id"]: mod.run_case_stub(c, bad=True) for c in cases}
    scores = mod._score_all(cases, runs)
    assert all(not s.passed for s in scores.values())
    assert all(
        any("numbers not from tools" in f for f in s.failures) for s in scores.values()
    )


def test_golden_filters():
    mod = _load_runner()
    assert [c["id"] for c in mod.load_golden(ids={"lookup_btc_price"})] == [
        "lookup_btc_price"
    ]
    assert all(
        c["category"] == "smalltalk" for c in mod.load_golden(categories={"smalltalk"})
    )
