"""Router eval harness 測試 — scripts/eval_router.py（Model Mixer Step 3）.

CI（無 key）釘住：資料集 schema 與覆蓋面、golden replay 100%、評分數學
（含危險錯誤的判定）、live 模式的 URL 安全守衛（https only／拒私有位址）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "eval_router", ROOT / "scripts" / "eval_router.py"
)
eval_router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_router)

DATASET = ROOT / "tests/eval/router_dataset.jsonl"
REPLAY = ROOT / "tests/eval/router_replay.jsonl"

pytestmark = pytest.mark.unit


class TestDataset:
    def test_loads_and_schema_valid(self):
        entries = eval_router.load_dataset(DATASET)
        assert len(entries) >= 120

    def test_covers_four_languages(self):
        entries = eval_router.load_dataset(DATASET)
        assert {e["language"] for e in entries} >= {"zh-TW", "zh-CN", "en", "ru"}

    def test_covers_dangerous_edges(self):
        """必含：已知漏判案例、gate 案例、寒暄＋金融混合、veto 案例。"""
        entries = eval_router.load_dataset(DATASET)
        ids = {e["id"] for e in entries}
        assert "zh-tw-fin-lookup-001" in ids  # 台積電本益比（§1 漏判案例）
        assert any(e["expected"]["gate"] == "risk_first" for e in entries)
        assert any(e["id"].startswith("zh-tw-mixed-") for e in entries)
        assert any(e["id"].startswith("zh-tw-fin-veto-") for e in entries)

    def test_rules_layer_consistency(self):
        """資料集與 triage 規則層一致：t0→lookup/[]、veto→research/null gate。"""
        from core.agents.triage import has_finance_signal, is_smalltalk

        for e in eval_router.load_dataset(DATASET):
            exp = e["expected"]
            if is_smalltalk(e["query"]):
                assert (exp["depth"], exp["domains"], exp["gate"]) == (
                    "lookup", [], None
                ), e["id"]
            elif has_finance_signal(e["query"]):
                assert (exp["depth"], exp["gate"]) == ("research", None), e["id"]

    def test_replay_covers_every_entry(self):
        entries = eval_router.load_dataset(DATASET)
        replay = eval_router.load_replay(REPLAY)
        assert set(replay) == {e["id"] for e in entries}


class TestGoldenReplay:
    def test_golden_replay_scores_100(self, capsys):
        """golden 回放（期望即回覆）→ 全指標 100%、危險錯誤 0。

        同時釘住 harness↔dataset↔router 三者一致——任何一邊改了語義，
        這裡就是第一個紅的。
        """
        rc = eval_router.main(
            ["--mode", "router", "--replay", str(REPLAY)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "depth: 100.0%" in out
        assert "dangerous (data→no-tool path): 0" in out


class TestLiveAdapter:
    async def test_async_http_is_cancellable_by_router_budget(self):
        from core.agents.router import route_query

        cancelled = asyncio.Event()

        async def slow_post(*args, **kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        with patch("httpx.AsyncClient.post", side_effect=slow_post):
            invoke = eval_router.build_live_invoke("https://example.com/v1", "fixture", "fixture")
            decision = await route_query("台積電本益比", invoke, timeout_s=0.02)
        assert decision.source == "fallback"
        assert cancelled.is_set()

    async def test_live_success(self):
        import httpx

        response = httpx.Response(200, request=httpx.Request("POST", "https://example.com"),
                                 json={"choices": [{"message": {"content": "reply"}}]})
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as post:
            invoke = eval_router.build_live_invoke("https://example.com/v1", "fixture", "fixture")
            assert await invoke("query") == "reply"
        assert post.call_args.kwargs["json"]["model"] == "fixture"


class TestLegacyBaseline:
    @pytest.mark.parametrize("query", ["HELLO", "OK", "你好"])
    async def test_t0_precedes_t1_finance_veto(self, query):
        invoke = AsyncMock(return_value="deep_analysis")
        replies, _ = await eval_router.run_t1([{"id": "one", "query": query}], lambda _: invoke)
        assert replies == {"one": "simple_qa"}
        invoke.assert_not_awaited()

    async def test_non_t0_still_uses_classifier(self):
        invoke = AsyncMock(return_value="simple_qa")
        replies, _ = await eval_router.run_t1(
            [{"id": "one", "query": "今天天氣如何"}], lambda _: invoke
        )
        assert replies == {"one": "simple_qa"}
        invoke.assert_awaited_once()


class TestScoringMath:
    def _mini_dataset(self, tmp_path: Path) -> Path:
        entries = [
            {
                "id": "m1",
                "query": "你好",
                "language": "zh-TW",
                "expected": {"domains": [], "depth": "lookup", "gate": None},
            },
            {
                "id": "m2",
                "query": "台積電本益比",
                "language": "zh-TW",
                "expected": {
                    "domains": ["finance_markets"],
                    "depth": "lookup",
                    "gate": None,
                },
            },
            {
                "id": "m3",
                "query": "哪家電動車品牌比較好",
                "language": "zh-TW",
                "expected": {
                    "domains": ["general_research"],
                    "depth": "analysis",
                    "gate": None,
                },
            },
        ]
        path = tmp_path / "mini.jsonl"
        path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8"
        )
        return path

    def test_dangerous_error_counted(self, tmp_path, capsys):
        """m3 期望 analysis+domain，模型回 lookup+[] → 危險錯誤 1。"""
        dataset = self._mini_dataset(tmp_path)
        replay = tmp_path / "r.jsonl"
        replies = {
            "m1": json.dumps({"domains": [], "depth": "lookup", "gate": None}),
            "m2": json.dumps(
                {"domains": ["finance_markets"], "depth": "lookup", "gate": None}
            ),
            # 危險：資料需求題被判成無工具可答的 fast-path 組合
            "m3": json.dumps({"domains": [], "depth": "lookup", "gate": None}),
        }
        replay.write_text(
            "\n".join(
                json.dumps({"id": k, "reply": v}, ensure_ascii=False)
                for k, v in replies.items()
            ),
            encoding="utf-8",
        )
        rc = eval_router.main(["--mode", "router", "--dataset", str(dataset), "--replay", str(replay)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dangerous (data→no-tool path): 1" in out
        assert "m3" in out  # miss 清單要列出該筆

    def test_t1_scoring(self, tmp_path):
        entries = eval_router.load_dataset(self._mini_dataset(tmp_path))
        # m1 應 simple（lookup 無 domain）；m2/m3 應 deep（有資料需求）
        assert eval_router.t1_expected_depth(entries[0]) == "simple_qa"
        assert eval_router.t1_expected_depth(entries[1]) == "deep_analysis"
        assert eval_router.t1_expected_depth(entries[2]) == "deep_analysis"


class TestLiveUrlGuard:
    def test_rejects_http(self):
        with pytest.raises(SystemExit):
            eval_router.validate_base_url("http://api.example.com/v1")

    def test_rejects_localhost(self):
        with pytest.raises(SystemExit):
            eval_router.validate_base_url("https://localhost:8080/v1")

    def test_rejects_private_address(self):
        with pytest.raises(SystemExit):
            with patch("socket.gethostbyname", return_value="10.0.0.5"):
                eval_router.validate_base_url("https://internal.example.com/v1")

    def test_accepts_public_https(self):
        with patch("socket.gethostbyname", return_value="104.18.0.10"):
            url = eval_router.validate_base_url("https://openrouter.ai/api/v1")
        assert url == "https://openrouter.ai/api/v1"


class TestRulesMode:
    def test_rules_report_buckets(self, capsys):
        rc = eval_router.main(["--mode", "rules"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"t0_cache"' in out and '"veto"' in out and '"needs_llm"' in out
