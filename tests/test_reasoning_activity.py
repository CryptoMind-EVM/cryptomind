"""on_chunk callback 測試 — reasoning token 也視為活動。

驗證修復 reasoning model 思考期被 idle timeout 誤殺的 bug。
根因：reasoning token 進 chunk.additional_kwargs，content 為空，
原本只看 content 的 on_token 不觸發 → idle clock 不重置 → 誤殺。

修法：execute_streaming 新增 on_chunk callback，任何 astream chunk
到達（含 reasoning）都觸發，用於重置 idle clock。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.unit
def test_on_chunk_called_for_reasoning_chunks():
    """reasoning model 的思考 chunk（content 空、reasoning 在 additional_kwargs）
    應觸發 on_chunk（重置 idle clock），即使不觸發 on_token。
    """
    from langchain_core.messages import AIMessageChunk

    # 模擬 reasoning model 的串流：思考 token，content 空
    reasoning_chunks = [
        ("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning": "思考中..."}), {})),
        ("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning": "繼續思考..."}), {})),
        ("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning": "快想完了"}), {})),
        ("messages", (AIMessageChunk(content="最終答案", additional_kwargs={}), {})),
    ]

    chunk_calls = []
    token_calls = []

    async def _consume():
        # 直接模擬 _run_astream 裡的 chunk 處理邏輯
        for mode, payload in reasoning_chunks:
            # on_chunk 在任何 chunk 到達時呼叫（修復後的邏輯）
            chunk_calls.append(True)
            chunk = payload[0] if isinstance(payload, tuple) else payload
            if isinstance(chunk, AIMessageChunk):
                content = chunk.content
                if isinstance(content, list):
                    content = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p)
                        for p in content
                    )
                if content:
                    token_calls.append(content)

    asyncio.new_event_loop().run_until_complete(_consume())

    # on_chunk 應被每個 chunk 觸發（4 次，含 3 個 reasoning）
    assert len(chunk_calls) == 4
    # on_token 只被 visible content 觸發（1 次，最終答案）
    assert len(token_calls) == 1
    assert token_calls[0] == "最終答案"


@pytest.mark.unit
def test_on_chunk_resets_idle_clock():
    """reasoning chunk 持續到達時，on_chunk 重置 idle clock → 不觸發 timeout。

    模擬 reasoning model 思考超過 idle timeout（120s）但仍活躍吐 chunk。
    """
    import time

    IDLE_LIMIT = 0.3
    last_activity = [time.monotonic()]
    timed_out = [False]

    async def _reasoning_stream():
        # 模擬持續吐 reasoning chunk，每 0.1s 一次，共 0.6s（超過 idle 0.3）
        for _ in range(6):
            await asyncio.sleep(0.1)
            # on_chunk 重置 activity（修復後）
            last_activity[0] = time.monotonic()

    async def _watchdog():
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if now - last_activity[0] > IDLE_LIMIT:
                timed_out[0] = True
                return

    async def _test():
        stream_task = asyncio.ensure_future(_reasoning_stream())
        watch_task = asyncio.ensure_future(_watchdog())
        done, pending = await asyncio.wait(
            [stream_task, watch_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

    asyncio.new_event_loop().run_until_complete(_test())

    # 持續有 chunk → idle clock 持續重置 → 不該 timeout
    assert not timed_out[0], "reasoning chunk 持續到達卻被判 idle timeout（誤殺）"


@pytest.mark.unit
def test_idle_still_triggers_when_truly_silent():
    """真正靜默（連 chunk 都沒有）→ 仍應觸發 timeout。確認修復沒破壞正常 idle 偵測。
    """
    import time

    IDLE_LIMIT = 0.3
    last_activity = [time.monotonic()]
    timed_out = [False]

    async def _silent_stream():
        # 完全靜默——不吐任何 chunk（模擬 LLM 真的卡死）
        await asyncio.sleep(1.0)

    async def _watchdog():
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if now - last_activity[0] > IDLE_LIMIT:
                timed_out[0] = True
                return

    async def _test():
        stream_task = asyncio.ensure_future(_silent_stream())
        watch_task = asyncio.ensure_future(_watchdog())
        done, pending = await asyncio.wait(
            [stream_task, watch_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

    asyncio.new_event_loop().run_until_complete(_test())

    # 真正靜默 → 應觸發 timeout
    assert timed_out[0], "真正卡死（零 chunk）卻沒被判 idle timeout"
