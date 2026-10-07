"""平台免費模型的結果全站共用（2026-09-28）＋回答標示模型、沒模型時導到 AI Studio。

- 市場分頁「AI 深度分析」：平台模型（local_llama）產生的報告同一檔同一語言共用一份；
  自帶金鑰的仍各自一份。同一 process 內同時觸發只算一次。
- 對話回答帶 model_label（平台模型只給品牌名，底層模型名不外露）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class _FakeCache:
    """ai_analysis_cache 的記憶體替身：記錄每次讀寫用的 owner。"""

    def __init__(self):
        self.store = {}
        self.owners = []

    async def get(self, market, symbol, user_id):
        self.owners.append(user_id)
        hit = self.store.get((market, symbol, user_id))
        return dict(hit) if hit else None

    async def put(self, market, symbol, user_id, result):
        self.store[(market, symbol, user_id)] = {**result, "cached_at": "now"}

    async def cooldown(self, market, symbol, user_id):
        return None

    async def set_cd(self, market, symbol, user_id):
        return None


def _patches(cache, calls, delay=0.0):
    async def fake_llm(symbol, context, key, provider, model, language="zh-TW"):
        calls.append((provider, symbol, language))
        if delay:
            await asyncio.sleep(delay)
        return f"report for {symbol}", None

    return (
        patch("core.ai_analysis_cache.get_cached_analysis", side_effect=cache.get),
        patch("core.ai_analysis_cache.cache_analysis", side_effect=cache.put),
        patch("core.ai_analysis_cache.check_cooldown", side_effect=cache.cooldown),
        patch("core.ai_analysis_cache.set_cooldown", side_effect=cache.set_cd),
        patch(
            "api.routers.deep_analysis_helper.deep_analyze_generic",
            side_effect=fake_llm,
        ),
    )


async def _run(provider, user_id, cache, calls, *, language="zh-TW", delay=0.0):
    from api.routers.deep_analysis_helper import get_deep_analysis

    p1, p2, p3, p4, p5 = _patches(cache, calls, delay)
    with p1, p2, p3, p4, p5:
        return await get_deep_analysis(
            market="usstock",
            symbol="NVDA",
            context="public market data",
            llm_key="local",
            llm_provider=provider,
            llm_model=None,
            user_id=user_id,
            language=language,
        )


class TestSharedDeepAnalysis:
    async def test_platform_model_report_is_shared_across_users(self):
        from core.platform_share import PLATFORM_OWNER as PLATFORM_CACHE_OWNER

        cache, calls = _FakeCache(), []
        first = await _run("local_llama", "user_a", cache, calls)
        second = await _run("local_llama", "user_b", cache, calls)
        assert len(calls) == 1, "第二個人直接拿共用的那份，不再叫模型"
        assert first["report"] == second["report"]
        assert set(cache.owners) == {PLATFORM_CACHE_OWNER}, (
            "平台模型不用 user_id 當快取擁有者"
        )

    async def test_language_still_separates_reports(self):
        cache, calls = _FakeCache(), []
        await _run("local_llama", "user_a", cache, calls, language="zh-TW")
        await _run("local_llama", "user_b", cache, calls, language="en")
        assert len(calls) == 2

    async def test_own_key_reports_stay_per_user(self):
        cache, calls = _FakeCache(), []
        await _run("openai", "user_a", cache, calls)
        await _run("openai", "user_b", cache, calls)
        assert len(calls) == 2, "自帶金鑰的各自一份（花的是自己的錢）"
        assert set(cache.owners) == {"user_a", "user_b"}

    async def test_concurrent_platform_requests_generate_once(self):
        from api.routers.deep_analysis_helper import get_deep_analysis

        cache, calls = _FakeCache(), []
        p1, p2, p3, p4, p5 = _patches(cache, calls, delay=0.05)
        with p1, p2, p3, p4, p5:  # mock 只套一次：並行的請求共用同一組替身
            results = await asyncio.gather(
                *[
                    get_deep_analysis(
                        market="usstock",
                        symbol="NVDA",
                        context="public market data",
                        llm_key="local",
                        llm_provider="local_llama",
                        user_id=f"user_{i}",
                    )
                    for i in range(5)
                ]
            )
        assert len(calls) == 1, "同一檔同時被 5 個人觸發也只算一次"
        assert all(r["report"] for r in results)


class TestAnswerModelLabel:
    def test_platform_model_shows_brand_only(self):
        from core.model_config import (
            LOCAL_LLAMA_DEFAULT_MODEL,
            PLATFORM_FREE_MODEL_LABEL,
            answer_model_label,
        )

        for model in ("cryptomind-lite", LOCAL_LLAMA_DEFAULT_MODEL, None):
            label = answer_model_label("local_llama", model)
            assert label == PLATFORM_FREE_MODEL_LABEL
            assert LOCAL_LLAMA_DEFAULT_MODEL not in label

    def test_byok_model_uses_display_name(self):
        from core.model_config import MODEL_CONFIG, answer_model_label

        model = MODEL_CONFIG["openai"]["available_models"][0]
        assert answer_model_label("openai", model["value"]) == model["display"]
        assert answer_model_label("openai", "some-new-model") == "some-new-model"

    def test_label_is_saved_and_sent_on_both_paths(self):
        api = (REPO / "api" / "routers" / "analysis.py").read_text(encoding="utf-8")
        assert 'response_metadata["model_label"] = model_label' in api
        assert '_meta = {"model_label": model_label}' in api
        worker = (REPO / "scripts" / "analysis_worker.py").read_text(encoding="utf-8")
        assert '"model_label": model_label' in worker

    def test_frontend_renders_label_live_and_in_history(self):
        js = (REPO / "web" / "js" / "chat-analysis.js").read_text(encoding="utf-8")
        assert "renderModelLabel(responseMetadata.model_label)" in js
        history = (REPO / "web" / "js" / "chat-history.js").read_text(encoding="utf-8")
        assert "window.renderModelLabel(msg.metadata.model_label)" in history


def test_no_model_prompts_point_to_ai_studio_models():
    analysis = (REPO / "web" / "js" / "chat-analysis.js").read_text(encoding="utf-8")
    sessions = (REPO / "web" / "js" / "chat-sessions.js").read_text(encoding="utf-8")
    assert "window.openModelSettings()" in analysis
    assert 'data-click="openModelSettings"' in sessions
    assert (
        "switchTab('settings')"
        not in analysis.split("getCachedUserProvider()", 1)[1][:1500]
    )
    state = (REPO / "web" / "js" / "chat-state.js").read_text(encoding="utf-8")
    assert "studio.showSection('models')" in state
    delegator = (REPO / "web" / "js" / "click-delegator.js").read_text(encoding="utf-8")
    assert "action === 'openModelSettings'" in delegator


class TestCryptoPulseShared:
    """加密貨幣 Market Pulse 深度分析：平台模型 1 小時內的結果全站共用。"""

    @staticmethod
    def _entry(provider, minutes_ago, mode="deep_analysis"):
        from datetime import datetime, timedelta, timezone

        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return {
            "symbol": "BTC",
            "source_mode": mode,
            "analyzed_by": provider,
            "timestamp": ts.isoformat(),
            "report": "x",
        }

    def test_fresh_platform_report_is_reused(self):
        from api.routers.market import helpers

        with patch.dict(
            helpers.MARKET_PULSE_CACHE, {"BTC": self._entry("local_llama", 10)}
        ):
            shared = helpers.shared_platform_deep_report("BTC")
        assert shared and shared["shared"] is True

    @pytest.mark.parametrize(
        "entry_args, refresh",
        [
            (("openai", 5), False),  # 別人自帶金鑰的不當共用來源
            (("local_llama", 90), False),  # 超過 1 小時
            (("local_llama", 20), True),  # 重新整理且已超過 15 分鐘冷卻
            (("local_llama", 5, "on_demand"), False),  # 不是深度分析
        ],
    )
    def test_not_reused(self, entry_args, refresh):
        from api.routers.market import helpers

        with patch.dict(helpers.MARKET_PULSE_CACHE, {"BTC": self._entry(*entry_args)}):
            assert helpers.shared_platform_deep_report("BTC", refresh) is None

    def test_refresh_within_cooldown_still_reuses(self):
        from api.routers.market import helpers

        with patch.dict(
            helpers.MARKET_PULSE_CACHE, {"BTC": self._entry("local_llama", 5)}
        ):
            assert helpers.shared_platform_deep_report("BTC", refresh=True)

    async def test_platform_model_runs_once_then_shares(self):
        from api.routers.market import helpers

        calls = []

        async def fake_deep(symbol, sources, key, provider):
            calls.append(provider)
            entry = self._entry(provider, 0)
            helpers.MARKET_PULSE_CACHE[symbol] = entry
            return entry

        platform = {"provider": "local_llama", "api_key": "local"}
        with (
            patch.dict(helpers.MARKET_PULSE_CACHE, {}, clear=True),
            patch.object(helpers, "perform_deep_analysis", side_effect=fake_deep),
        ):
            await helpers.deep_analysis_shared_or_run("BTC", "", platform)
            second = await helpers.deep_analysis_shared_or_run("BTC", "", platform)
            own = {"provider": "openai", "api_key": "sk-x"}
            await helpers.deep_analysis_shared_or_run("BTC", "", own)
        assert calls == ["local_llama", "openai"], (
            "平台模型第二次直接共用；自帶金鑰照常自己算"
        )
        assert second["shared"] is True
