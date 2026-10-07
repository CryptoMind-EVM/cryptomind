"""Tests for the claw loop heartbeat（L4 可觀測性）。

設計契約：
- 沒有工具呼叫的長 LLM 期間，後端要持續送 progress，讓前端階段標籤有內容
  （線上實測「你好」有連續 44 秒畫面全靜止）
- 一旦開始串流就停止 heartbeat——畫面自然會動，再蓋文字會覆蓋掉 synthesizing
- heartbeat 純輔助：emit 失敗不可影響主流程
- 超過 DEEP_ANALYSIS_HINT_AFTER_SECONDS 改送帶時間預期的訊息
"""

from __future__ import annotations

import asyncio

import pytest

from core.agents.manager.claw_loop import (
    DEEP_ANALYSIS_HINT_AFTER_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _StreamSanitizer,
)

pytestmark = pytest.mark.unit


class TestStreamSanitizerHasEmitted:
    """heartbeat 靠 has_emitted 判斷「畫面是不是已經在動了」。"""

    def test_starts_false(self):
        assert _StreamSanitizer().has_emitted is False

    def test_stays_false_when_nothing_safe_to_emit(self):
        """buffer 太短（全在 safety tail 內）時不算已 emit。"""
        s = _StreamSanitizer(sanitizer_fn=lambda x: x)
        s.feed("ab")
        assert s.has_emitted is False

    def test_becomes_true_after_real_emit(self):
        s = _StreamSanitizer(sanitizer_fn=lambda x: x)
        # 餵夠長的內容，確保超出 safety tail 有東西可以送
        emitted = s.feed("x" * 500)
        assert emitted
        assert s.has_emitted is True

    def test_empty_chunk_does_not_flip_flag(self):
        s = _StreamSanitizer(sanitizer_fn=lambda x: x)
        s.feed("")
        assert s.has_emitted is False


class TestHeartbeatConstants:
    def test_interval_is_perceptible_but_not_flickery(self):
        assert 1 <= HEARTBEAT_INTERVAL_SECONDS <= 5

    def test_hint_threshold_is_a_multiple_of_interval(self):
        """門檻不是間隔的倍數的話，切換點會落在兩次 heartbeat 之間而延遲觸發。"""
        assert DEEP_ANALYSIS_HINT_AFTER_SECONDS % HEARTBEAT_INTERVAL_SECONDS == 0

    def test_hint_comes_after_several_plain_heartbeats(self):
        assert DEEP_ANALYSIS_HINT_AFTER_SECONDS > HEARTBEAT_INTERVAL_SECONDS


class TestHeartbeatMessagesExist:
    """heartbeat 用到的 i18n key 必須 4 語齊全（governance test 只驗結構）。"""

    @pytest.mark.parametrize("language", ["zh-TW", "zh-CN", "en", "ru"])
    @pytest.mark.parametrize(
        "key",
        [
            "ui_messages.progress.still_thinking",
            "ui_messages.progress.deep_analysis_hint",
        ],
    )
    def test_key_resolves(self, key, language):
        from core.i18n import t

        resolved = t(key, language)
        # 未知 key 時 t() 會退回 key 字串本身
        assert resolved != key
        assert resolved.strip()

    def test_still_thinking_has_no_redundant_timer(self):
        """前端 #loading-timer 已顯示已花時間，訊息不該再重複秒數。"""
        from core.i18n import t

        for language in ("zh-TW", "zh-CN", "en", "ru"):
            assert "{seconds}" not in t(
                "ui_messages.progress.still_thinking", language
            )


@pytest.mark.asyncio
class TestHeartbeatLoopBehaviour:
    """用一個縮小版迴圈驗證行為契約（不啟整個 graph）。"""

    async def test_stops_once_streaming_starts(self):
        """開始串流後 heartbeat 必須停，否則會蓋掉 synthesizing 標籤。"""
        sanitizer = _StreamSanitizer(sanitizer_fn=lambda x: x)
        emitted: list[str] = []

        async def heartbeat():
            while True:
                await asyncio.sleep(0.01)
                if sanitizer.has_emitted:
                    return
                emitted.append("tick")

        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.035)
        ticks_before = len(emitted)
        assert ticks_before >= 1, "串流開始前應該要有 heartbeat"

        sanitizer.feed("y" * 500)  # 模擬第一個 token 送出
        await task  # 應該自己結束，不需要 cancel

        await asyncio.sleep(0.03)
        assert len(emitted) == ticks_before, "串流開始後不該再有 heartbeat"

    async def test_cancellation_is_clean(self):
        """agent 結束時 cancel heartbeat，不可往外拋 CancelledError。"""

        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.02)
        task.cancel()
        await task  # 乾淨收尾，不該 raise
        assert task.done()
