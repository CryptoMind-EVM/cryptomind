"""客戶端斷線後，分析要繼續跑完並存檔，而不是被取消。

使用者回報：手機切到別的 App 再切回來就出現 network error，內容全沒了。
原因是 SSE generator 結束時 `invoke_task.cancel()` 把分析殺掉，而存檔是在
分析完成之後才做 —— 於是工作丟失、什麼都沒留下。

手機切換 App 必然斷線，取消等於每次切出去都白跑。改成交給背景收尾。
代價是任務脫離連線後沒人看著，所以閒置監控必須在伺服器端。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from api.routers import analysis

ANALYSIS_SOURCE = Path(analysis.__file__).read_text(encoding="utf-8")


class _FakeManager:
    def __init__(self):
        self.progress_callback = lambda event: None


@pytest.mark.asyncio
async def test_detached_run_saves_result_after_disconnect(monkeypatch):
    """斷線後分析跑完，結果要寫進 DB。"""
    saved = {}

    def fake_save(role, content, session_id=None, user_id=None, metadata=None):
        saved.update(
            {"role": role, "content": content, "session_id": session_id, "user_id": user_id}
        )

    monkeypatch.setattr(analysis, "save_chat_message", fake_save)

    async def slow_analysis():
        await asyncio.sleep(0.05)
        return {"final_response": "背景跑完的分析結果"}

    task = asyncio.create_task(slow_analysis())
    manager = _FakeManager()

    await analysis._finish_analysis_detached(
        task,
        {"at": asyncio.get_running_loop().time()},
        manager=manager,
        session_id="sess-detached",
        user_id="user-1",
        language="zh-TW",
    )

    assert saved["role"] == "assistant"
    assert saved["content"] == "背景跑完的分析結果"
    assert saved["session_id"] == "sess-detached"
    # 收尾後要把 callback 清掉，避免留在共用的 manager 上
    assert manager.progress_callback is None


@pytest.mark.asyncio
async def test_detached_run_is_cancelled_when_idle_too_long(monkeypatch):
    """閒置超過上限要中止，否則卡住的任務會一直燒 token。"""
    monkeypatch.setattr(analysis, "DETACHED_IDLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(analysis, "_DETACHED_IDLE_CHECK_INTERVAL_SECONDS", 0.01)

    saved = {}
    monkeypatch.setattr(
        analysis,
        "save_chat_message",
        lambda *a, **k: saved.setdefault("called", True),
    )

    async def stuck_analysis():
        await asyncio.sleep(30)
        return {"final_response": "never"}

    task = asyncio.create_task(stuck_analysis())
    # 時間戳停在很久以前 → 一開始就算閒置
    stale = {"at": 0.0}

    await analysis._finish_analysis_detached(
        task,
        stale,
        manager=_FakeManager(),
        session_id="sess-stuck",
        user_id="user-1",
        language="zh-TW",
    )

    assert task.cancelled() or task.done()
    assert "called" not in saved, "卡住被中止的分析不該存檔"


@pytest.mark.asyncio
async def test_detached_run_does_not_save_when_no_final_response(monkeypatch):
    """HITL 中斷沒有 final_response，不可寫進對話紀錄。"""
    saved = {}
    monkeypatch.setattr(
        analysis,
        "save_chat_message",
        lambda *a, **k: saved.setdefault("called", True),
    )

    async def interrupted():
        return {"__interrupt__": [object()]}

    await analysis._finish_analysis_detached(
        asyncio.create_task(interrupted()),
        {"at": asyncio.get_running_loop().time()},
        manager=_FakeManager(),
        session_id="sess-hitl",
        user_id="user-1",
        language="zh-TW",
    )

    assert "called" not in saved


@pytest.mark.asyncio
async def test_disconnect_detaches_only_once(monkeypatch):
    """斷線時 except CancelledError 與 finally 都會呼叫 detach_analysis——
    只能交接一次，否則起兩個收尾協程，同一則回覆存進對話紀錄兩次。

    走真正的 analyze_crypto → event_generator_v4：跑到 graph 執行中時
    取消消費串流的 task（= Starlette 在客戶端斷線時做的事），再放行分析。
    """
    import importlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from starlette.requests import Request as StarletteRequest

    from api.models import QueryRequest

    analysis._local_analysis_runs.clear()
    monkeypatch.setattr(analysis.limiter, "enabled", False)

    release = asyncio.Event()

    async def slow_ainvoke(graph_input, config):
        await release.wait()
        return {"final_response": "斷線後才跑完的回答"}

    manager = SimpleNamespace(
        graph=SimpleNamespace(ainvoke=slow_ainvoke), progress_callback=None
    )
    monkeypatch.setattr(
        importlib.import_module("core.agents.bootstrap"),
        "bootstrap",
        MagicMock(return_value=manager),
    )
    saved: list = []
    monkeypatch.setattr(
        analysis,
        "save_chat_message",
        lambda role, content, **kw: saved.append((role, content)),
    )
    monkeypatch.setattr(analysis, "get_chat_history", MagicMock(return_value=[]))
    monkeypatch.setattr(analysis, "set_current_session", MagicMock())
    monkeypatch.setattr(analysis, "get_session_owner", MagicMock(return_value=None))
    monkeypatch.setattr(
        "core.database.user.get_user_display_name", lambda _: "Fixture"
    )
    # principal 查錢包會連 DB；沒有 DB 時連線池重試 10×3 秒，整個測試像卡死
    monkeypatch.setattr(
        "core.database.user.get_verified_evm_address", lambda _: None
    )
    monkeypatch.setattr("core.analysis_queue.enqueue_job", lambda _: False)
    monkeypatch.setattr(analysis.shared_cache, "set_json", MagicMock())
    monkeypatch.setattr(analysis.shared_cache, "get_json", MagicMock(return_value=None))
    monkeypatch.setattr(
        analysis, "_resolve_preset_model_override", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        analysis, "_resolve_agent_preset_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        analysis, "_resolve_router_llm_client", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(
        analysis,
        "resolve_user_llm_credentials",
        AsyncMock(
            return_value={
                "provider": "openai",
                "model": "gpt-5.4",
                "api_key": "fixture-key",
            }
        ),
    )
    monkeypatch.setattr(
        analysis,
        "create_user_llm_client",
        MagicMock(return_value=SimpleNamespace(model_name="gpt-5.4")),
    )

    spawned: list = []
    real_spawn = analysis._spawn_detached

    def counting_spawn(coro):
        task = real_spawn(coro)
        spawned.append(task)
        return task

    monkeypatch.setattr(analysis, "_spawn_detached", counting_spawn)

    request = StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/analyze",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    body = QueryRequest(
        message="fixture query",
        session_id="sess-detach-once",
        user_provider="openai",
        system_prompt="",
        enabled_tools=[],
    )
    response = await analysis.analyze_crypto(
        request=request,
        body=body,
        current_user={"user_id": "user-1", "membership_tier": "premium"},
        session=None,
    )
    stream = response.body_iterator
    first = await stream.__anext__()
    assert "run_started" in first

    # 串流停在「等 graph 跑完」那一步時客戶端斷線
    consumer = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert len(spawned) == 1, f"交接了 {len(spawned)} 次——回覆會重複存檔"

    release.set()
    await asyncio.gather(*spawned)
    assistant_saves = [c for r, c in saved if r == "assistant"]
    assert assistant_saves == ["斷線後才跑完的回答"]
    analysis._local_analysis_runs.clear()


def test_disconnect_path_detaches_instead_of_cancelling():
    """接線檢查：斷線分支必須交接給背景，不可再取消任務。

    上面的行為測試直接呼叫 _finish_analysis_detached，驗的是收尾協程本身；
    如果有人把 generator 的斷線分支改回 invoke_task.cancel()，那些測試仍會通過。
    這個測試守的就是那條接線。
    """
    generator = re.search(
        r"async def event_generator_v4\(\):(.*?)\n        return StreamingResponse",
        ANALYSIS_SOURCE,
        re.S,
    )
    assert generator, "找不到 event_generator_v4 —— 測試需要更新"

    # 抓整個 CancelledError 分支（到下一個 except 為止）。
    # 舊 regex 用 `(.*?)\n\s*raise` 會停在「revoked 分支」的第一個 raise——
    # 後來新增的撤銷授權分支（run["revoked"] 的 if/raise）破壞了它，
    # 導致抓到的片段不含 detach_analysis（程式碼本身是對的）。
    match = re.search(
        r"except asyncio\.CancelledError:\s*\n(.*?)(?=\n\s*except )",
        generator.group(1),
        re.S,
    )
    assert match, "找不到 generator 的斷線分支 —— 測試需要更新"

    branch = match.group(1)
    assert "detach_analysis" in branch, (
        "斷線分支沒有呼叫 detach_analysis：客戶端一斷線分析就會被丟掉"
    )
    assert "invoke_task.cancel()" not in branch, (
        "斷線分支又在取消任務了 —— 手機切換 App 就會讓分析白跑"
    )


def test_frontend_treats_connection_loss_as_background_run():
    """前端接線：連線中斷不可顯示成錯誤。

    伺服器端已經改成斷線不取消分析，如果前端還把 TypeError（Chrome 的
    「Failed to fetch」、Safari 的「Load failed」、部分 WebView 的
    「network error」）當成失敗顯示紅字，使用者仍會以為分析掛了。
    """
    source = (
        Path(__file__).resolve().parents[1] / "web" / "js" / "chat-analysis.js"
    ).read_text(encoding="utf-8")

    assert "function isConnectionLostError" in source, "缺少連線中斷的判斷"
    assert "isConnectionLostError(err)" in source, (
        "catch 分支沒有先判斷連線中斷 —— 斷線會被顯示成一般錯誤"
    )
    assert "watchForBackgroundResult" in source, (
        "斷線後沒有安排取回背景分析結果"
    )

    # 連線中斷分支必須排在通用錯誤分支之前，否則永遠走不到
    lost_at = source.index("isConnectionLostError(err)")
    generic_at = source.index("normalizeChatErrorMessage(\n                    err?.message")
    assert lost_at < generic_at, "連線中斷判斷排在通用錯誤之後，永遠不會生效"


def test_idle_timeout_is_server_side():
    """閒置保護必須在伺服器端，客戶端走了之後才還有人看著。"""
    assert "DETACHED_IDLE_TIMEOUT_SECONDS" in ANALYSIS_SOURCE
    assert re.search(
        r"idle_for\s*>\s*DETACHED_IDLE_TIMEOUT_SECONDS", ANALYSIS_SOURCE
    ), "找不到伺服器端的閒置判斷"
