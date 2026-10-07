"""聊天室 AI 助理 API（api/routers/chat_assistant.py）：開關、權限、次數、頻率、排隊、SSE。

repo 與 LLM 換成假的（repo 在 test_chat_assistant_repo 用真 PG 測、回答在 test_chat_assistant_answer 測），
直接呼叫 endpoint.__wrapped__（跳過 limiter），同 test_group_chat_router 的做法。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc)


def _req():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )


class FakeRepo:
    def __init__(self):
        self.messages = [
            {
                "id": 1,
                "name": "小明",
                "text": "BTC 要回測 6 萬",
                "type": "text",
                "created_at": NOW,
                "reply_to_name": None,
            }
        ]
        self.fetch_calls = []
        self.used = 0
        self.refunds = 0
        self.consume_calls = []

    async def fetch_messages(self, kind, target_id, user_id, range_="recent", **kw):
        self.fetch_calls.append((kind, target_id, user_id, range_, kw))
        if target_id == 404:
            return {"success": False, "error": "not_found"}
        return {"success": True, "messages": list(self.messages)}

    async def consume_quota(self, user_id, limit, session=None):
        self.consume_calls.append(limit)
        if limit is not None and self.used >= limit:
            return {"ok": False, "used": limit, "remaining": 0}
        self.used += 1
        return {
            "ok": True,
            "used": self.used,
            "remaining": None if limit is None else limit - self.used,
        }

    async def refund_quota(self, user_id, session=None):
        self.refunds += 1
        self.used -= 1

    async def used_today(self, user_id, session=None):
        return self.used


class FakeHistory:
    """問答紀錄 repo 的替身（真 PG 版在 test_chat_assistant_history）"""

    def __init__(self):
        self.saved = []
        self.fail = False
        self.visible = True
        self.turns = []
        self.deleted = []
        self.cleared = []

    async def save_turn(
        self, kind, target_id, user_id, question, answer, source_ids, meta, **kw
    ):
        if self.fail:
            raise RuntimeError("db down")
        turn = {
            "id": len(self.saved) + 1,
            "question": question,
            "answer": answer,
            "meta": meta,
            "created_at": NOW,
        }
        self.saved.append(
            {
                "kind": kind,
                "target_id": target_id,
                "user_id": user_id,
                "source_ids": list(source_ids),
                "turn": turn,
            }
        )
        return turn

    async def list_turns(self, kind, target_id, user_id, **kw):
        if not self.visible:
            return {"success": False, "error": "not_found"}
        return {"success": True, "turns": list(self.turns)}

    async def delete_turn(self, turn_id, user_id, **kw):
        self.deleted.append((turn_id, user_id))
        return turn_id == 7

    async def clear(self, kind, target_id, user_id, **kw):
        self.cleared.append((kind, target_id, user_id))
        return 3


@pytest.fixture
def mod(monkeypatch):
    from api.routers import chat_assistant as mod

    repo = FakeRepo()
    history = FakeHistory()
    monkeypatch.setattr(mod, "chat_assistant_repo", repo)
    monkeypatch.setattr(mod, "chat_assistant_history_repo", history)
    monkeypatch.setattr(
        mod, "get_user_membership", lambda uid: {"is_premium": uid.startswith("pro")}
    )

    async def config(key, default=None, session=None):
        return {"chat_assistant_enabled": True, "limit_ai_assistant_free_daily": 2}.get(
            key, default
        )

    monkeypatch.setattr(mod.config_repo, "get_config", config)

    async def no_own_key(user, preferred_provider=None):
        return None

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", no_own_key)
    monkeypatch.setattr(
        mod,
        "fallback_credentials",
        lambda tier="free": {
            "provider": "local_llama",
            "api_key": "",
            "model": "m",
            "local": True,
        },
    )
    built = []
    monkeypatch.setattr(
        mod, "create_user_llm_client", lambda **kw: built.append(kw) or object()
    )
    answered = []

    async def fake_answer(client, question, history, language, context, deadline):
        answered.append(
            {
                "question": question,
                "history": history,
                "language": language,
                "context": context,
            }
        )
        return "大家在聊 BTC", "agent"

    monkeypatch.setattr(mod, "answer", fake_answer)
    monkeypatch.setattr(mod, "_PLATFORM_SEM", asyncio.Semaphore(1))
    mod._IN_FLIGHT.clear()
    mod._PENDING.clear()
    mod._test = {
        "repo": repo,
        "history": history,
        "built": built,
        "answered": answered,
    }
    yield mod
    mod._IN_FLIGHT.clear()
    mod._PENDING.clear()


def _body(mod, **kw):
    data = {"kind": "dm", "target_id": 5, "question": "在聊什麼？"} | kw
    return mod.AskRequest(**data)


async def _ask(mod, user="free-1", **kw):
    return await mod.ask_assistant.__wrapped__(
        _req(), _body(mod, **kw), current_user={"user_id": user}
    )


async def _events(response):
    raw = ""
    async for chunk in response.body_iterator:
        raw += chunk if isinstance(chunk, str) else chunk.decode()
    return [
        json.loads(part[len("data: ") :])
        for part in raw.split("\n\n")
        if part.startswith("data: ")
    ]


# ── 守衛 ──────────────────────────────────────────────


def test_limiter_decorators_and_registration():
    lines = (
        (REPO / "api/routers/chat_assistant.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    limiter_lines = [
        i for i, line in enumerate(lines) if line.startswith("@limiter.limit")
    ]
    routes = [
        line for line in lines if re.match(r"@router\.(get|post|delete)\(", line)
    ]
    assert len(routes) == len(limiter_lines) == 5
    for i in limiter_lines:
        assert lines[i - 1].startswith("@router."), (
            f"第 {i + 1} 行：@limiter 要緊接在 @router 下面"
        )
    assert "chat_assistant_router" in (REPO / "api_server.py").read_text(
        encoding="utf-8"
    )
    assert "dependencies=[Depends(require_chat_assistant_enabled)]" in "\n".join(lines)


def test_ask_has_minute_and_hour_limits():
    import api.routers.chat_assistant  # noqa: F401 — import 時才向共用 limiter 登記
    from api.middleware.rate_limit import limiter

    limits = limiter._route_limits.get("api.routers.chat_assistant.ask_assistant", [])
    assert {str(item.limit) for item in limits} >= {"3 per 1 minute", "30 per 1 hour"}


async def test_flag_off_is_404(monkeypatch, mod):
    async def off(key, default=None, session=None):
        return False

    monkeypatch.setattr(mod.config_repo, "get_config", off)
    with pytest.raises(HTTPException) as exc:
        await mod.require_chat_assistant_enabled()
    assert exc.value.status_code == 404


# ── 輸入驗證 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "kw",
    [
        {"question": "x" * 501},
        {"question": "   "},
        {"range": "unread"},  # 缺 from_id／unread_count
        {"range": "unread", "unread_count": 0},
        {"range": "message"},  # 缺 around_id
        {"history": [{"role": "user", "content": "q"}] * 7},
        {"history": [{"role": "system", "content": "q"}]},
        {"kind": "channel"},
    ],
)
def test_bad_bodies_rejected(mod, kw):
    with pytest.raises(ValidationError):
        _body(mod, **kw)


# ── 權限 ──────────────────────────────────────────────


async def test_not_participant_is_404_and_no_quota(mod):
    with pytest.raises(HTTPException) as exc:
        await _ask(mod, target_id=404)
    assert exc.value.status_code == 404 and mod._test["repo"].consume_calls == []
    assert not mod._IN_FLIGHT, "擋下的請求要放掉「進行中」"


async def test_group_requires_pro(mod):
    with pytest.raises(HTTPException) as exc:
        await _ask(mod, kind="group")
    assert exc.value.status_code == 403 and exc.value.detail == "pro_required"
    events = await _events(await _ask(mod, user="pro-1", kind="group"))
    assert events[-1]["type"] == "answer"


# ── 次數 ──────────────────────────────────────────────


async def test_free_dm_daily_limit(mod):
    for _ in range(2):
        assert (await _events(await _ask(mod)))[-1]["type"] == "answer"
    with pytest.raises(HTTPException) as exc:
        await _ask(mod)
    assert exc.value.status_code == 429 and exc.value.detail == "quota_exhausted"
    assert mod._test["repo"].consume_calls == [2, 2], "第三次在進串流前就擋下，沒扣"


async def test_quota_taken_once_even_if_nobody_reads_the_response(mod):
    """產生答案在背景任務跑：沒人讀回應（斷線）照樣跑完、扣一次、存起來，不會白扣也不會漏扣"""
    await _ask(mod)
    await asyncio.wait_for(asyncio.gather(*mod._TASKS), 2)
    assert mod._test["repo"].used == 1 and mod._test["repo"].refunds == 0
    assert len(mod._test["history"].saved) == 1
    assert not mod._IN_FLIGHT and not mod._PENDING


async def test_quota_race_inside_stream(monkeypatch, mod):
    """進來時還有額度、真的要扣時已經滿了（另一個分頁剛用掉）→ SSE 錯誤、不回答"""
    repo = mod._test["repo"]

    async def zero(user_id, session=None):
        return 0

    monkeypatch.setattr(repo, "used_today", zero)
    repo.used = 2
    events = await _events(await _ask(mod))
    assert events == [{"type": "error", "code": "quota_exhausted"}]
    assert mod._test["answered"] == [] and repo.refunds == 0


async def test_unread_count_passed_to_repo(mod):
    await _events(await _ask(mod, range="unread", unread_count=3))
    kind, target, user, range_, kw = mod._test["repo"].fetch_calls[0]
    assert range_ == "unread" and kw["unread_count"] == 3 and kw["from_id"] is None


async def test_pro_recorded_without_limit(mod):
    events = await _events(await _ask(mod, user="pro-1"))
    assert mod._test["repo"].consume_calls == [None]
    assert events[-1]["meta"]["remaining"] is None


async def test_own_key_not_counted_and_used(monkeypatch, mod):
    async def own(user, preferred_provider=None):
        return {"provider": "deepseek", "api_key": "sk-x", "model": "deepseek-chat"}

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", own)
    monkeypatch.setattr(
        mod, "fallback_credentials", lambda tier="free": pytest.fail("不該用平台模型")
    )
    events = await _events(await _ask(mod))
    assert events[-1]["type"] == "answer" and events[-1]["meta"]["model"] == "deepseek"
    assert mod._test["repo"].consume_calls == []
    assert (
        mod._test["built"][0]["provider"] == "deepseek"
        and mod._test["built"][0]["api_key"] == "sk-x"
    )


async def test_no_model_is_400(monkeypatch, mod):
    monkeypatch.setattr(mod, "fallback_credentials", lambda tier="free": None)
    with pytest.raises(HTTPException) as exc:
        await _ask(mod)
    assert exc.value.status_code == 400 and exc.value.detail == "no_model"


# ── 同時一題、排隊 ────────────────────────────────────


async def test_one_question_at_a_time(mod):
    first = await _ask(mod, user="pro-1")
    with pytest.raises(HTTPException) as exc:
        await _ask(mod, user="pro-1")
    assert exc.value.status_code == 429 and exc.value.detail == "assistant_busy_self"
    await _events(first)  # 跑完就放掉
    assert (await _events(await _ask(mod, user="pro-1")))[-1]["type"] == "answer"


async def test_stale_in_flight_entry_ignored(mod):
    mod._IN_FLIGHT["pro-1"] = (
        asyncio.get_running_loop().time() - mod.IN_FLIGHT_STALE_SECONDS - 1
    )
    assert (await _events(await _ask(mod, user="pro-1")))[-1]["type"] == "answer"


async def test_platform_busy_refunds(monkeypatch, mod):
    monkeypatch.setattr(mod, "_PLATFORM_SEM", asyncio.Semaphore(0))
    monkeypatch.setattr(mod, "QUEUE_WAIT_SECONDS", 0.05)
    events = await _events(await _ask(mod))
    assert [e.get("stage") for e in events[:2]] == ["reading", "queued"]
    assert events[-1] == {"type": "error", "code": "busy"}
    assert mod._test["repo"].used == 0 and mod._test["repo"].refunds == 1
    assert not mod._IN_FLIGHT


async def test_own_key_skips_platform_queue(monkeypatch, mod):
    async def own(user, preferred_provider=None):
        return {"provider": "openai", "api_key": "sk", "model": "gpt"}

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", own)
    monkeypatch.setattr(mod, "_PLATFORM_SEM", asyncio.Semaphore(0))
    events = await _events(await _ask(mod))
    assert (
        "queued" not in [e.get("stage") for e in events]
        and events[-1]["type"] == "answer"
    )


# ── SSE 內容 ──────────────────────────────────────────


async def test_stream_order_and_context(mod):
    events = await _events(
        await _ask(
            mod,
            history=[
                {"role": "user", "content": "前一題"},
                {"role": "assistant", "content": "前一答"},
            ],
            language="en",
            tz="UTC",
        )
    )
    assert [e.get("stage") for e in events if e["type"] == "stage"] == [
        "reading",
        "queued",
        "thinking",
    ]
    final = events[-1]
    assert final["type"] == "answer" and final["text"] == "大家在聊 BTC"
    assert final["meta"] == {
        "messages_used": 1,
        "truncated": False,
        "remaining": 1,
        "model": mod.PLATFORM_FREE_MODEL_LABEL,
    }
    call = mod._test["answered"][0]
    assert (
        "[10-01 06:30] 小明: BTC 要回測 6 萬" in call["context"]
        and call["language"] == "en"
    )
    assert call["history"] == [
        {"role": "user", "content": "前一題"},
        {"role": "assistant", "content": "前一答"},
    ]
    kind, target, user, range_, kw = mod._test["repo"].fetch_calls[0]
    assert (kind, target, user, range_) == ("dm", 5, "free-1", "recent")


async def test_long_range_condenses_with_progress(monkeypatch, mod):
    mod._test["repo"].messages = [
        {
            "id": i,
            "name": "小明",
            "text": "很長的討論" * 40,
            "type": "text",
            "created_at": NOW,
            "reply_to_name": None,
        }
        for i in range(400)
    ]
    calls = []

    async def fake_condense(client, chunks, language, deadline, on_progress):
        calls.append(len(chunks))
        return [f"重點{len(calls)}"]

    monkeypatch.setattr(mod, "condense_chunks", fake_condense)
    events = await _events(await _ask(mod, user="pro-1", range="3d"))
    progress = [
        (e["done"], e["total"]) for e in events if e.get("stage") == "condensing"
    ]
    assert (
        progress == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
        and calls == [1] * 5
    )
    assert events[-1]["meta"]["truncated"] is True
    assert "[part 1] 重點1" in mod._test["answered"][0]["context"]


async def test_answer_failure_refunds_and_reports(monkeypatch, mod):
    from core.chat_assistant.answer import AssistantFailed

    async def broken(*args, **kwargs):
        raise AssistantFailed("ConnectionError")

    monkeypatch.setattr(mod, "answer", broken)
    events = await _events(await _ask(mod))
    assert events[-1] == {"type": "error", "code": "llm_failed"}
    assert mod._test["repo"].refunds == 1 and not mod._IN_FLIGHT


async def test_own_key_failure_code(monkeypatch, mod):
    from core.chat_assistant.answer import AssistantFailed

    async def own(user, preferred_provider=None):
        return {"provider": "openai", "api_key": "sk", "model": "gpt"}

    async def broken(*args, **kwargs):
        raise AssistantFailed("AuthenticationError")

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", own)
    monkeypatch.setattr(mod, "answer", broken)
    events = await _events(await _ask(mod))
    assert events[-1] == {"type": "error", "code": "own_key_failed"}
    assert mod._test["repo"].refunds == 0, "自帶 key 本來就沒扣"


# ── 問答紀錄（跨裝置接續） ─────────────────────────────


async def test_answer_is_saved_with_sources(mod):
    mod._test["repo"].messages = [
        {"id": 1, "name": "小明", "text": "BTC 要回測 6 萬", "type": "text",
         "created_at": NOW, "reply_to_name": None},
        {"id": 2, "name": None, "text": "小華 joined the group", "type": "system",
         "created_at": NOW, "reply_to_name": None},
        {"id": 3, "name": "小華", "text": "我也這樣想", "type": "text",
         "created_at": NOW, "reply_to_name": None},
    ]  # fmt: skip
    events = await _events(await _ask(mod, range="24h"))
    final = events[-1]
    saved = mod._test["history"].saved
    assert final["type"] == "answer" and final["turn_id"] == saved[0]["turn"]["id"] == 1
    entry = saved[0]
    assert (entry["kind"], entry["target_id"], entry["user_id"]) == ("dm", 5, "free-1")
    assert entry["source_ids"] == [1, 3], "系統事件不會被收回，不記"
    turn = entry["turn"]
    assert turn["question"] == "在聊什麼？" and turn["answer"] == "大家在聊 BTC"
    assert turn["meta"] == {
        "messages_used": 3,
        "truncated": False,
        "model": mod.PLATFORM_FREE_MODEL_LABEL,
        "range": "24h",
    }, "剩餘次數是當下的事，不存"


async def test_save_failure_does_not_break_the_answer(mod):
    mod._test["history"].fail = True
    events = await _events(await _ask(mod))
    assert events[-1]["type"] == "answer" and events[-1]["turn_id"] is None
    assert events[-1]["text"] == "大家在聊 BTC"


async def test_failed_answer_is_not_saved(monkeypatch, mod):
    from core.chat_assistant.answer import AssistantFailed

    async def broken(*args, **kwargs):
        raise AssistantFailed("ConnectionError")

    monkeypatch.setattr(mod, "answer", broken)
    await _events(await _ask(mod))
    assert mod._test["history"].saved == []


async def test_unexpected_crash_reports_error_and_frees_the_user(monkeypatch, mod):
    async def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "answer", crash)
    events = await _events(await _ask(mod))
    assert events[-1] == {"type": "error", "code": "llm_failed"}
    assert mod._test["history"].saved == []
    assert not mod._IN_FLIGHT and not mod._PENDING
    assert mod._test["repo"].refunds == 1, "沒回答成功，退回次數"


async def test_closing_the_drawer_does_not_stop_generation(monkeypatch, mod):
    """關掉抽屜（斷線）只是不再讀回應：答案照樣產生、存起來；回來打開看得到「還在整理」，再回來看得到答案"""
    gate = asyncio.Event()

    async def slow(client, question, history, language, context, deadline):
        await gate.wait()
        return "整理好了", "agent"

    monkeypatch.setattr(mod, "answer", slow)
    response = await _ask(mod)
    iterator = response.body_iterator
    assert "reading" in await anext(iterator)
    await iterator.aclose()  # 使用者按了 ✕

    user = {"user_id": "free-1"}
    during = await mod.assistant_history.__wrapped__(
        _req(), kind="dm", target_id=5, current_user=user
    )
    assert during["pending"]["question"] == "在聊什麼？"
    assert isinstance(during["pending"]["elapsed"], int)
    assert mod._test["history"].saved == []
    assert mod._test["repo"].refunds == 0

    gate.set()
    await asyncio.wait_for(asyncio.gather(*mod._TASKS), 2)
    assert [e["turn"]["answer"] for e in mod._test["history"].saved] == ["整理好了"]
    assert mod._test["repo"].used == 1 and mod._test["repo"].refunds == 0
    after = await mod.assistant_history.__wrapped__(
        _req(), kind="dm", target_id=5, current_user=user
    )
    assert after["pending"] is None and not mod._IN_FLIGHT


async def test_history_returns_turns_and_pending_only_for_that_chat(mod):
    mod._test["history"].turns = [{"id": 1, "question": "問", "answer": "答"}]
    mod._PENDING["free-1"] = {
        "kind": "group",
        "target_id": 9,
        "question": "別的聊天室的題",
        "started": asyncio.get_running_loop().time() - 12.4,
    }
    user = {"user_id": "free-1"}
    here = await mod.assistant_history.__wrapped__(
        _req(), kind="dm", target_id=5, current_user=user
    )
    assert here == {"turns": mod._test["history"].turns, "pending": None}
    there = await mod.assistant_history.__wrapped__(
        _req(), kind="group", target_id=9, current_user=user
    )
    assert there["pending"]["question"] == "別的聊天室的題"
    assert 12 <= there["pending"]["elapsed"] <= 13


async def test_history_404_when_chat_not_visible(mod):
    mod._test["history"].visible = False
    with pytest.raises(HTTPException) as exc:
        await mod.assistant_history.__wrapped__(
            _req(), kind="dm", target_id=5, current_user={"user_id": "free-1"}
        )
    assert exc.value.status_code == 404


async def test_history_flag_off_is_404(monkeypatch, mod):
    async def off(key, default=None, session=None):
        return False

    monkeypatch.setattr(mod.config_repo, "get_config", off)
    with pytest.raises(HTTPException) as exc:
        await mod.require_chat_assistant_enabled()
    assert exc.value.status_code == 404


async def test_clear_and_delete_turn(mod):
    user = {"user_id": "free-1"}
    cleared = await mod.clear_assistant_history.__wrapped__(
        _req(), kind="group", target_id=9, current_user=user
    )
    assert cleared == {"success": True, "cleared": 3}
    assert mod._test["history"].cleared == [("group", 9, "free-1")]
    assert await mod.delete_assistant_turn.__wrapped__(
        _req(), 7, current_user=user
    ) == {"success": True}
    with pytest.raises(HTTPException) as exc:
        await mod.delete_assistant_turn.__wrapped__(_req(), 8, current_user=user)
    assert exc.value.status_code == 404, "不是自己的（或不存在）一律 404"
    assert mod._test["history"].deleted == [(7, "free-1"), (8, "free-1")]


# ── status ────────────────────────────────────────────


async def test_status(monkeypatch, mod):
    status = await mod.assistant_status.__wrapped__(
        _req(), current_user={"user_id": "free-1"}
    )
    assert status == {
        "own_key": False,
        "is_pro": False,
        "daily_limit": 2,
        "used_today": 0,
        "remaining": 2,
        "model_label": mod.PLATFORM_FREE_MODEL_LABEL,
        "platform_model": True,
        "model_provider": None,
    }
    pro = await mod.assistant_status.__wrapped__(
        _req(), current_user={"user_id": "pro-1"}
    )
    assert (
        pro["daily_limit"] is None
        and pro["remaining"] is None
        and pro["is_pro"] is True
    )


async def test_status_platform_model_chosen_in_settings_is_not_called_own_key(monkeypatch, mod):
    """在設定選了平台免費模型（local_llama）：顯示品牌名、標成平台模型——
    以前寫「使用你綁定的 local_llama」（2026-10-01 DANNY：顯示怪怪的）。次數規則照舊"""

    async def local(user, preferred_provider=None):
        return {"provider": "local_llama", "api_key": ""}

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", local)
    status = await mod.assistant_status.__wrapped__(_req(), current_user={"user_id": "free-1"})
    assert status["platform_model"] is True
    assert status["model_label"] == mod.PLATFORM_FREE_MODEL_LABEL
    assert "local_llama" not in str(status["model_label"])
    assert status["own_key"] is True, "次數規則不變：走自帶 credentials 的那條"


async def test_status_own_key_reports_provider(monkeypatch, mod):
    async def deepseek(user, preferred_provider=None):
        return {"provider": "deepseek", "api_key": "sk-test"}

    monkeypatch.setattr(mod, "resolve_user_llm_credentials", deepseek)
    status = await mod.assistant_status.__wrapped__(_req(), current_user={"user_id": "free-1"})
    assert status["platform_model"] is False
    assert status["model_provider"] == "deepseek"
