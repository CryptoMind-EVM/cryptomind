"""SSE 斷點續傳：重連後從上次收到的事件繼續，而不是重頭或輪詢。

L1 讓分析在斷線後繼續跑，L2 讓客戶端查得到當下輸出，
L3 讓重連可以接回同一條事件流 —— 這是 ChatGPT/Claude 的做法：
生成不綁在單一連線上，重連時從斷點續傳。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from api.routers import analysis

ANALYSIS_SOURCE = Path(analysis.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_runs():
    analysis._local_analysis_runs.clear()
    yield
    analysis._local_analysis_runs.clear()


def _emit(run, n, prefix="t"):
    for i in range(n):
        analysis._emit_run_event(run, {"type": "token", "content": f"{prefix}{i}"})


def test_events_carry_incrementing_ids():
    run = analysis._create_analysis_run("sess-1", "user-1")
    frame1 = analysis._emit_run_event(run, {"type": "token", "content": "a"})
    frame2 = analysis._emit_run_event(run, {"type": "token", "content": "b"})

    assert frame1.startswith("id: 1\n"), frame1
    assert frame2.startswith("id: 2\n"), frame2
    assert "data: " in frame1


def test_replay_returns_only_events_after_given_id():
    run = analysis._create_analysis_run("sess-1", "user-1")
    _emit(run, 5)

    frames, needs_resync = analysis._replay_run_events(run, 3)

    assert needs_resync is False
    assert len(frames) == 2, "應該只重播 id 4、5"
    assert "id: 4" in frames[0]
    assert "id: 5" in frames[1]


def test_replay_from_zero_returns_everything():
    run = analysis._create_analysis_run("sess-1", "user-1")
    _emit(run, 3)

    frames, needs_resync = analysis._replay_run_events(run, 0)

    assert needs_resync is False
    assert len(frames) == 3


def test_replay_asks_for_resync_when_buffer_no_longer_covers_position():
    """緩衝區被裁掉之後無法逐筆重播，必須改用整段內容補齊。

    否則中間會漏掉一段，使用者看到的內容就是錯的 —— 比乾等更糟。
    """
    run = analysis._create_analysis_run("sess-1", "user-1")
    _emit(run, analysis._MAX_REPLAY_EVENTS + 50)

    # 事件緩衝要有上限，不能無限成長
    assert len(run["events"]) <= analysis._MAX_REPLAY_EVENTS

    frames, needs_resync = analysis._replay_run_events(run, 1)
    assert needs_resync is True, "位置已被裁掉卻回報可以逐筆重播"
    assert frames == []


def test_emit_fans_out_to_subscribers():
    import asyncio

    run = analysis._create_analysis_run("sess-1", "user-1")
    queue: asyncio.Queue = asyncio.Queue()
    run["_subscribers"].add(queue)

    analysis._emit_run_event(run, {"type": "token", "content": "live"})

    assert queue.qsize() == 1
    frame = queue.get_nowait()
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["content"] == "live"


def test_emit_tolerates_missing_run():
    """例外可能在 run 建立之前發生，那時仍要能把錯誤送出去。"""
    frame = analysis._emit_run_event(None, {"error": "boom", "done": True})
    assert frame.startswith("data: "), frame
    assert "id:" not in frame


def test_events_are_not_written_to_shared_cache(monkeypatch):
    """逐筆事件不可進 Redis —— 量太大，跨 worker 靠 content 快照就好。"""
    captured = {}
    monkeypatch.setattr(
        analysis.shared_cache,
        "set_json",
        lambda key, value, ttl: captured.update({"value": value}),
    )

    run = analysis._create_analysis_run("sess-1", "user-1")
    _emit(run, 3)
    analysis._sync_analysis_run(run, force=True)

    assert "events" not in captured["value"], "事件緩衝被寫進共用快取了"
    assert "_subscribers" not in captured["value"]


def test_resume_endpoint_checks_ownership_and_uses_last_event_id():
    """接線檢查：續傳 endpoint 的權限與位置來源。"""
    endpoint = re.search(
        r"async def resume_analysis_stream\((.*?)\n    return StreamingResponse\(resume_generator",
        ANALYSIS_SOURCE,
        re.S,
    )
    assert endpoint, "找不到續傳 endpoint —— 測試需要更新"

    body = endpoint.group(1)
    assert 'snapshot.get("user_id") != current_user.get("user_id")' in body, (
        "續傳沒有比對 user_id：別人拿到 run_id 就能讀走整段分析"
    )
    assert "status_code=404" in body, "無權存取應回 404，不可洩漏 run_id 是否存在"
    assert 'request.headers.get("last-event-id")' in body, (
        "沒有讀 Last-Event-ID —— EventSource 自動重連會失去位置"
    )
    assert "keep-alive" in body, "沒有心跳，中間的代理可能因靜默關掉連線"


def test_frontend_tracks_event_id_and_resumes():
    """前端接線：要記住事件 id 並用它續傳。"""
    source = (
        Path(__file__).resolve().parents[1] / "web" / "js" / "chat-analysis.js"
    ).read_text(encoding="utf-8")

    assert "line.startsWith('id: ')" in source, "沒有解析事件 id，續傳會從頭開始"
    assert "function resumeAnalysisStream" in source, "沒有續傳實作"
    assert "/api/analyze/stream/" in source, "沒有連上續傳 endpoint"
    assert "?after=" in source, "首次重連沒有帶位置"
    assert "'resync'" in source, "沒有處理 resync（緩衝區蓋不到時的補齊）"
    # EventSource 不可用時要能退回輪詢，不能整個功能消失
    assert "watchForBackgroundResult(sessionId, runId, statusEl)" in source, (
        "EventSource 不可用時沒有退路"
    )
