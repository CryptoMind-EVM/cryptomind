"""Telegram bot 長回覆處理 — 回歸測試。

原本 handle_text 用 progressive edit 逐段 edit 同一則訊息，超過 4096 字元時
edit 會失敗並 `break`，錯誤全被吞掉，使用者永遠停在「🌀 分析中...」。
這裡守住：長回覆一定完整送達，且失敗時不會留下佔位訊息。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.telegram_bot import (
    _TG_MESSAGE_LIMIT,
    _keep_typing,
    _replace_placeholder,
    _split_chunks,
)

pytestmark = pytest.mark.unit


# --- _split_chunks ---------------------------------------------------------


def test_short_text_stays_one_chunk():
    assert _split_chunks("hello") == ["hello"]


def test_every_chunk_is_within_the_telegram_limit():
    text = "\n".join(f"line {i} " + "x" * 200 for i in range(200))
    chunks = _split_chunks(text)
    assert len(chunks) > 1
    assert all(len(c) <= _TG_MESSAGE_LIMIT for c in chunks)


def test_nothing_is_dropped_when_splitting():
    text = "\n".join(f"段落 {i} " + "字" * 300 for i in range(100))
    rejoined = "\n".join(_split_chunks(text))
    assert rejoined == text


def test_a_single_unbroken_line_longer_than_the_limit_is_hard_split():
    text = "x" * (_TG_MESSAGE_LIMIT * 2 + 17)
    chunks = _split_chunks(text)
    assert all(len(c) <= _TG_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks) == text


def test_boundary_exactly_at_the_limit_is_not_split():
    assert _split_chunks("y" * _TG_MESSAGE_LIMIT) == ["y" * _TG_MESSAGE_LIMIT]


# --- _replace_placeholder --------------------------------------------------


def _fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot


def test_short_reply_edits_the_placeholder_and_sends_nothing_extra():
    bot = _fake_bot()
    asyncio.run(_replace_placeholder(bot, 1, 42, "答案"))
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["text"] == "答案"
    bot.send_message.assert_not_awaited()


def test_long_reply_is_delivered_in_full_not_truncated():
    bot = _fake_bot()
    reply = "\n".join(f"重點 {i} " + "分" * 300 for i in range(60))
    asyncio.run(_replace_placeholder(bot, 1, 42, reply))

    delivered = [bot.edit_message_text.await_args.kwargs["text"]]
    delivered += [c.kwargs["text"] for c in bot.send_message.await_args_list]
    assert "\n".join(delivered) == reply
    assert len(delivered) > 1, "長回覆應該被分成多則送出"


def test_failed_edit_falls_back_to_sending_so_the_user_is_never_left_hanging():
    bot = _fake_bot()
    bot.edit_message_text.side_effect = RuntimeError("Bad Request: message is too long")
    asyncio.run(_replace_placeholder(bot, 1, 42, "答案"))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["text"] == "答案"


# --- _keep_typing ----------------------------------------------------------


def test_keep_typing_repeats_and_stops_cleanly_on_cancel():
    bot = _fake_bot()

    async def scenario():
        task = asyncio.create_task(_keep_typing(bot, 1))
        await asyncio.sleep(0.01)
        task.cancel()
        await task  # must not raise — the handler cancels this in `finally`

    asyncio.run(scenario())
    bot.send_chat_action.assert_awaited()
