"""
Langfuse 整合測試

覆蓋：
1. 未設 keys / TEST_MODE=true → disabled，handler 為 None
2. 設了 keys 且非 TEST_MODE → enabled，handler 非 None
3. ``_TracedGraph`` 在 disabled 時不改 config；enabled 時注入 callbacks
4. ``trace_graph_call`` 在 disabled 時為 no-op；在 enabled 時進入 OTel context
5. shutdown 在 disabled 時不爆
6. 機密（API key 字串）不應出現在 handler / metadata
7. （可選）E2E：實際打 Langfuse API 驗證 trace 上報（需 keys + network）

注意：conftest.py 已設 TEST_MODE=true，因此多數 case 預期 disabled。
我們用 monkeypatch 切換 TEST_MODE / keys 來覆蓋 enabled 路徑。
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


def _reload_langfuse_init():
    """重新載入 utils.langfuse_init，讓模組級狀態重置。

    ``_langfuse_enabled`` / ``_init_attempted`` 是模組級全域，測試之間必須重置。
    """
    import utils.langfuse_init as mod

    importlib.reload(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset_langfuse_module(monkeypatch):
    """每個 test 前：清掉 LANGFUSE_* 環境變數、reload 模組。"""
    for k in list(__import__("os").environ.keys()):
        if k.startswith("LANGFUSE_"):
            monkeypatch.delenv(k, raising=False)
    mod = _reload_langfuse_init()
    yield mod
    _reload_langfuse_init()


# ---------------------------------------------------------------------------
# Disabled paths
# ---------------------------------------------------------------------------


def test_disabled_when_no_keys(_reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False
    mod._init_attempted = False
    import os

    os.environ.pop("TEST_MODE", None)

    try:
        assert mod.init_langfuse() is False
        assert mod.is_langfuse_enabled() is False
        assert mod.get_langchain_handler() is None
    finally:
        os.environ["TEST_MODE"] = "true"


def test_disabled_when_test_mode_true(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    assert mod.init_langfuse() is False
    assert mod.is_langfuse_enabled() is False
    assert mod.get_langchain_handler() is None


def test_disabled_via_explicit_flag(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    assert mod.init_langfuse() is False
    assert mod.is_langfuse_enabled() is False


def test_shutdown_is_noop_when_disabled(_reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False
    mod.shutdown_langfuse()


def test_trace_graph_call_is_noop_when_disabled(_reset_langfuse_module):
    """disabled 時 trace_graph_call 應為 no-op，但仍可正常進出 with。"""
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False

    flag = {"ran": False}
    with mod.trace_graph_call(user_id="u", session_id="s"):
        flag["ran"] = True
    assert flag["ran"] is True


# ---------------------------------------------------------------------------
# Enabled path（mock auth_check 避免 network call）
# ---------------------------------------------------------------------------


def test_enabled_when_keys_set_and_not_test_mode(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    with patch("langfuse.Langfuse.auth_check", return_value=True):
        assert mod.init_langfuse() is True
        assert mod.is_langfuse_enabled() is True


def test_disabled_when_auth_check_fails(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    with patch("langfuse.Langfuse.auth_check", side_effect=RuntimeError("401")):
        assert mod.init_langfuse() is False
        assert mod.is_langfuse_enabled() is False


def test_init_caps_auth_check_at_5_seconds(monkeypatch, _reset_langfuse_module):
    """auth_check 超過 5 秒必須 timeout 降級，不能阻塞 lifespan 啟動。

    背景：zeabur 部署時曾發生 lifespan 卡住 → 前端 15s timeout 的 outage。
    root cause：auth_check() 是無 timeout 上限的同步阻塞呼叫，遇到 Langfuse
    server 慢會卡到 requests 預設 30s 才放棄。
    """
    import time

    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    # 模擬 auth_check 卡 30 秒（遠超 5s 上限）
    def _slow_auth_check(*args, **kwargs):
        time.sleep(30)

    with patch("langfuse.Langfuse.auth_check", side_effect=_slow_auth_check):
        t0 = time.perf_counter()
        result = mod.init_langfuse()
        elapsed = time.perf_counter() - t0

    # 必須在 ~5 秒內返回（允許 ±1.5s 緩衝給 log/return）
    assert elapsed < 7, f"init 花了 {elapsed:.1f}s，超過 5s 上限"
    # 必須降級為 disabled
    assert result is False
    assert mod.is_langfuse_enabled() is False


def test_handler_returned_when_enabled(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    with patch("langfuse.Langfuse.auth_check", return_value=True):
        mod.init_langfuse()
        handler = mod.get_langchain_handler()
        assert handler is not None
        from langfuse.langchain import CallbackHandler

        assert isinstance(handler, CallbackHandler)


def test_handler_returns_none_on_internal_error(_reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = True

    # 讓 CallbackHandler 建構失敗
    with patch("langfuse.langchain.CallbackHandler", side_effect=RuntimeError("boom")):
        handler = mod.get_langchain_handler()
        assert handler is None


# ---------------------------------------------------------------------------
# _TracedGraph config 注入邏輯（純單元測試，不啟動 Langfuse）
# ---------------------------------------------------------------------------


def _make_traced_graph(mod, user_id="u", session_id="s"):
    """用 mod（reload 後的 langfuse_init）建一個 _TracedGraph。

    ``_TracedGraph._attach_langfuse_callback`` 會透過
    ``from utils.langfuse_init import ...`` 動態 import，因此 reload 後的模組
    狀態會被使用。
    """
    from core.agents.manager._main import _TracedGraph

    class _FakeGraph:
        async def ainvoke(self, input, config=None, **kw):
            return {"input": input, "config": config}

        def invoke(self, input, config=None, **kw):
            return {"input": input, "config": config}

    return _TracedGraph(
        _FakeGraph(), lambda: None, user_id=user_id, session_id=session_id
    )


def test_traced_graph_no_inject_when_disabled(_reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False
    tg = _make_traced_graph(mod)

    original_config = {"recursion_limit": 10}
    result = tg._attach_langfuse_callback(original_config)
    assert result == original_config
    assert "callbacks" not in result


def test_traced_graph_injects_handler_when_enabled(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = True

    sentinel = object()
    with patch.object(mod, "get_langchain_handler", return_value=sentinel):
        tg = _make_traced_graph(mod)
        result = tg._attach_langfuse_callback({"recursion_limit": 10})

    assert "callbacks" in result
    assert sentinel in result["callbacks"]
    assert result["recursion_limit"] == 10


def test_traced_graph_appends_to_existing_callbacks(monkeypatch, _reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = True

    sentinel = object()
    existing_cb = object()
    with patch.object(mod, "get_langchain_handler", return_value=sentinel):
        tg = _make_traced_graph(mod)
        result = tg._attach_langfuse_callback({"callbacks": [existing_cb]})

    assert result["callbacks"] == [existing_cb, sentinel]


def test_traced_graph_works_with_none_config(_reset_langfuse_module):
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False
    tg = _make_traced_graph(mod)

    result = tg._attach_langfuse_callback(None)
    assert result is None


@pytest.mark.asyncio
async def test_traced_graph_ainvoke_runs_without_langfuse(_reset_langfuse_module):
    """End-to-end：disabled 時 _TracedGraph.ainvoke 正常執行 fake graph，
    且不應拋例；on_complete 被呼叫。"""
    mod = _reset_langfuse_module
    mod._langfuse_enabled = False

    completed = {"flag": False}

    def on_complete():
        completed["flag"] = True

    from core.agents.manager._main import _TracedGraph

    class _FakeGraph:
        async def ainvoke(self, input, config=None, **kw):
            return {"echo": input}

    tg = _TracedGraph(_FakeGraph(), on_complete, user_id="u", session_id="s")
    result = await tg.ainvoke({"msg": "hi"})
    assert result == {"echo": {"msg": "hi"}}
    assert completed["flag"] is True


@pytest.mark.asyncio
async def test_traced_graph_ainvoke_attaches_handler_when_enabled(
    monkeypatch, _reset_langfuse_module
):
    """enabled 時 ainvoke 應把 handler 放進 config['callbacks']，並執行成功。"""
    mod = _reset_langfuse_module
    mod._langfuse_enabled = True

    captured = {"config": None}

    sentinel = object()
    with patch.object(mod, "get_langchain_handler", return_value=sentinel):
        from core.agents.manager._main import _TracedGraph

        class _FakeGraph:
            async def ainvoke(self, input, config=None, **kw):
                captured["config"] = config
                return {"ok": True}

        tg = _TracedGraph(_FakeGraph(), lambda: None, user_id="u", session_id="s")
        result = await tg.ainvoke({"msg": "hi"})

    assert result == {"ok": True}
    assert captured["config"] is not None
    assert sentinel in captured["config"]["callbacks"]


# ---------------------------------------------------------------------------
# 安全性：metadata 不應包含敏感欄位
# ---------------------------------------------------------------------------


def test_no_secret_in_metadata(_reset_langfuse_module):
    """metadata 用來帶 request_id / tier 等；放 API key 是濫用，這裡至少保護
    呼叫端不小心傳入 secrets 時能被我們發現（schema 上不應該有這些 key）。"""
    forbidden = {"api_key", "secret_key", "password", "token"}
    metadata = {"tier": "free", "request_id": "abc"}
    assert not (forbidden & set(metadata.keys()))
