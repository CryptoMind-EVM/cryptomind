"""
Tests for analysis_queue Redis queue + pub/sub layer.

These tests verify the graceful degradation logic (no Redis → fallback to False/None)
and the key/channel naming conventions. Full Redis integration tests require a live
Redis instance (skipped in CI without REDIS_URL).
"""

import pytest

from core.analysis_queue import (
    _control_channel,
    _event_channel,
    dequeue_job,
    enqueue_job,
    publish_control,
    publish_event,
    reset,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """每個測試前重置 Redis 連線狀態。"""
    reset()
    yield
    reset()


class TestChannelNaming:
    """Redis channel 命名慣例。"""

    def test_event_channel_format(self):
        assert _event_channel("abc123") == "analysis:events:abc123"

    def test_control_channel_format(self):
        assert _control_channel("abc123") == "analysis:control:abc123"


class TestGracefulDegradation:
    """Redis 不可用時的優雅降級（無 REDIS_URL 環境）。"""

    def test_enqueue_returns_false_without_redis(self):
        """無 Redis → enqueue 回 False（API fallback 到 in-process）。"""
        result = enqueue_job({"run_id": "test", "session_id": "s1"})
        assert result is False

    def test_dequeue_returns_none_without_redis(self):
        """無 Redis → dequeue 回 None（worker 不該跑）。"""
        result = dequeue_job(timeout=1)
        assert result is None

    def test_publish_event_silent_without_redis(self):
        """無 Redis → publish_event 不 raise（靜默 no-op）。"""
        # 不該 raise 任何例外
        publish_event("test-run", {"type": "token", "data": {"chunk": "hi"}})

    def test_publish_control_returns_false_without_redis(self):
        """無 Redis → publish_control 回 False（revoke fallback 到 in-process）。"""
        result = publish_control("test-run", "revoke")
        assert result is False


class TestJobEnvelopeFormat:
    """Job envelope 格式驗證（analysis.py 的 _build_job_envelope）。"""

    def test_envelope_is_json_serializable(self):
        """Job envelope 的所有欄位都是 JSON 可序列化的。"""
        import json

        envelope = {
            "run_id": "abc123",
            "session_id": "session-1",
            "user_id": "user-1",
            "language": "zh-TW",
            "user_tier": "free",
            "display_name": "Danny",
            "wallet_address": "0x1234...",
            "credentials": {"provider": "openrouter", "api_key": "sk-...", "model": "test"},
            "key_fingerprint": "abc12345",
            "web_mode": "web",
            "graph_input": {"goto": "claw_loop", "update": {"query": "test"}},
            "config": {"configurable": {"thread_id": "session-1"}, "recursion_limit": 60},
            "resume_answer": None,
            "created_at": 1234567890.0,
        }

        # 必須能 JSON 序列化（Redis queue 傳輸用）
        serialized = json.dumps(envelope)
        deserialized = json.loads(serialized)
        assert deserialized["run_id"] == "abc123"
        assert deserialized["credentials"]["provider"] == "openrouter"
        assert deserialized["graph_input"]["goto"] == "claw_loop"


class _FakePubSub:
    def __init__(self, events):
        import orjson

        self._msgs = [{"type": "message", "data": orjson.dumps(e)} for e in events]
        self.unsubscribed = False

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=0.0):
        import asyncio

        if self._msgs:
            return self._msgs.pop(0)
        await asyncio.sleep(10)  # 沒有下一則：迭代沒停就會卡在這
        return None


class TestSubscribeEventsTermination:
    """worker 的收尾幀沒有 type（{"done": True}、{"error": ..., "done": True}）。"""

    @pytest.mark.parametrize(
        "final",
        [{"done": True}, {"done": True, "waiting": True}, {"error": "x", "done": True}],
    )
    async def test_stops_after_untyped_done_frame(self, monkeypatch, final):
        import asyncio

        from core import analysis_queue

        pubsub = _FakePubSub([{"type": "run_started"}, final])

        class _Client:
            def pubsub(self):
                return pubsub

        async def _client():
            return _Client()

        monkeypatch.setattr(analysis_queue, "_get_async_client", _client)

        async def _collect():
            return [
                e
                async for e in analysis_queue.subscribe_events("r1", timeout=0.05)
                if e is not None
            ]

        events = await asyncio.wait_for(_collect(), timeout=2)
        assert events == [{"type": "run_started"}, final], "收尾幀要先交給呼叫端再停"
        assert pubsub.unsubscribed


class _RedisLikePubSub:
    """行為對齊 redis-py 的 async PubSub.get_message：沒訊息時等滿 timeout 才回
    None；不帶 timeout（預設 0.0）就立刻回 None。為了測試快，等待上限 0.05 秒。"""

    def __init__(self, messages=(), *, silent_calls=0):
        import orjson

        self._silent = silent_calls
        self._msgs = [{"type": "message", "data": orjson.dumps(m)} for m in messages]
        self.timeouts = []

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel):
        pass

    async def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        import asyncio

        self.timeouts.append(timeout)
        if self._silent > 0 or not self._msgs:
            self._silent -= 1
            await asyncio.sleep(min(timeout or 0.0, 0.05))
            return None
        return self._msgs.pop(0)


def _patch_client(monkeypatch, pubsub):
    from core import analysis_queue

    class _Client:
        def pubsub(self):
            return pubsub

    async def _client():
        return _Client()

    monkeypatch.setattr(analysis_queue, "_get_async_client", _client)


class TestPubSubActuallyBlocks:
    """get_message 預設 timeout=0.0 立刻回 None：外層 wait_for 永遠等不到逾時，
    迴圈空轉吃滿 CPU、心跳也從沒發出去。timeout 必須交給 get_message 自己等。"""

    async def test_silent_channel_yields_heartbeats_instead_of_spinning(
        self, monkeypatch
    ):
        import asyncio

        from core import analysis_queue

        pubsub = _RedisLikePubSub()
        _patch_client(monkeypatch, pubsub)

        async def _first_three():
            out = []
            async for event in analysis_queue.subscribe_events("r1", timeout=0.05):
                out.append(event)
                if len(out) == 3:
                    break
            return out

        # 修之前：get_message 立刻回 None → continue，永遠不 yield → 這裡逾時
        events = await asyncio.wait_for(_first_three(), timeout=2)
        assert events == [None, None, None], "靜默期間要發心跳（yield None）"
        assert pubsub.timeouts == [0.05, 0.05, 0.05], (
            "每次心跳對應一次真的阻塞等待，不是空轉上千次"
        )

    async def test_control_listener_blocks_and_survives_silence(self, monkeypatch):
        import asyncio

        from core import analysis_queue

        pubsub = _RedisLikePubSub([{"action": "revoke"}], silent_calls=3)
        _patch_client(monkeypatch, pubsub)
        revoked = []

        await asyncio.wait_for(
            analysis_queue.listen_for_control("r1", lambda: revoked.append(True)),
            timeout=2,
        )
        assert revoked == [True], "靜默一段時間後的撤銷仍要收到"
        assert len(pubsub.timeouts) == 4
        assert all(t and t > 0 for t in pubsub.timeouts), (
            f"get_message 沒帶 timeout 會空轉：{pubsub.timeouts}"
        )


class TestRunContract:
    """API 與 worker 共用的 run 規則（TTL、錯誤訊息過濾）。"""

    def test_run_status_ttl_covers_whole_analysis(self, monkeypatch):
        from core.analysis_queue import run_status_ttl_seconds

        monkeypatch.delenv("ANALYSIS_RUN_TTL_SECONDS", raising=False)
        monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "3600")
        assert run_status_ttl_seconds() == 3900
        monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "60")
        assert run_status_ttl_seconds() == 900, "下限 900 秒"

    @pytest.mark.parametrize(
        "timeout, resume_window",
        [("3600", "900"), ("60", "900"), ("3600", "7200"), ("60", "1800")],
    )
    def test_run_status_ttl_outlives_hitl_resume_window(
        self, monkeypatch, timeout, resume_window
    ):
        """HITL 暫停時寫的 run key 要撐過續傳時限（ANALYSIS_RUN_TTL_SECONDS）。

        worker 只用 run_status_ttl_seconds()；續傳時限調大而 TTL 沒跟上時，
        key 先過期，使用者在時限內回答 → run 查不到（404）、前端當成已消失。
        """
        from core.analysis_queue import run_status_ttl_seconds

        monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", timeout)
        monkeypatch.setenv("ANALYSIS_RUN_TTL_SECONDS", resume_window)
        assert run_status_ttl_seconds() >= int(resume_window)

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Error code: 401 - Unauthorized (host 10.0.0.5)", "Invalid API key"),
            ("You exceeded your current quota (host 10.0.0.5)", "usage limit"),
            ("ReadTimeout: pool(host='10.0.0.5'): Read timed out", "timed out"),
            ("psycopg2.OperationalError: connection to 10.0.0.5 failed", "An error"),
        ],
    )
    def test_safe_error_message_never_echoes_raw(self, raw, expected):
        from core.analysis_queue import safe_analysis_error_message

        msg = safe_analysis_error_message(raw)
        assert expected in msg
        assert "10.0.0.5" not in msg, "內部細節不能出現在給使用者的訊息"
