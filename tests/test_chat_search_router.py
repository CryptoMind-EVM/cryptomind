"""聊天搜尋 API（api/routers/chat_search.py）：q 驗證、群組開關、限流守衛、註冊。

可見範圍在 test_chat_search_repo 用真 PG 測；這裡 repo 換成假的，直接呼叫
endpoint.__wrapped__（跳過 limiter），同 test_group_chat_router 的做法。
最後一段接真 PG 走一次 endpoint，確認開關關掉時私訊照常、群組回空。
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）

REPO = Path(__file__).resolve().parents[1]
ME = {"user_id": "me", "username": "me"}


def _req():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/chat-search",
            "headers": [],
            "client": ("t", 1),
        }
    )


@pytest.fixture
def router(monkeypatch):
    from api.routers import chat_search as router

    calls = []

    async def fake_search(user_id, query, include_groups=True, session=None):
        calls.append((user_id, query, include_groups))
        return {"contacts": [], "groups": [], "messages": []}

    async def flag_on(key, default=None, session=None):
        return True

    monkeypatch.setattr(router.chat_search_repo, "search", fake_search)
    monkeypatch.setattr(router.config_repo, "get_config", flag_on)
    router._test_calls = calls
    return router


async def _call(router, q):
    return await router.chat_search.__wrapped__(_req(), q=q, current_user=ME)


# ── 守衛 ──────────────────────────────────────────────


def test_limiter_decorator_is_below_router_decorator():
    """@limiter 在 @router 上面時 router 登記的是沒包 limiter 的函式，限流等於沒有"""
    lines = (
        (REPO / "api/routers/chat_search.py").read_text(encoding="utf-8").splitlines()
    )
    routes = [
        i for i, line in enumerate(lines) if re.match(r"@router\.(get|post)\(", line)
    ]
    assert routes, "找不到路由"
    for i in routes:
        assert lines[i + 1].startswith('@limiter.limit("30/minute")'), (
            f"第 {i + 2} 行：@limiter 要緊接在 @router 下面"
        )


def test_router_registered():
    src = (REPO / "api_server.py").read_text(encoding="utf-8")
    assert "from api.routers.chat_search import router as chat_search_router" in src
    assert "app.include_router(chat_search_router)" in src


# ── q 驗證 ──────────────────────────────────────────────


@pytest.mark.parametrize("q", ["", "   ", "\n\t", "x" * 51, " " + "字" * 51 + " "])
async def test_invalid_query_is_400(router, q):
    with pytest.raises(HTTPException) as exc:
        await _call(router, q)
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_query"
    assert router._test_calls == [], "驗證沒過不該查 DB"


async def test_query_is_stripped_before_search(router):
    res = await _call(router, "  " + "字" * 50 + "  ")
    assert res == {"success": True, "contacts": [], "groups": [], "messages": []}
    assert router._test_calls == [("me", "字" * 50, True)]


def test_query_raw_length_capped_by_fastapi():
    """去空白前的原始長度也有上限（FastAPI 擋，422）"""
    import inspect

    from api.routers.chat_search import chat_search

    q = inspect.signature(chat_search).parameters["q"].default
    assert any(getattr(m, "max_length", None) == 200 for m in q.metadata)


def test_http_validation_and_rate_limit(router):
    """走真的 FastAPI：缺 q／原始過長是 422、去空白後空的是 400、第 31 次被限流"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi.errors import RateLimitExceeded

    from api.deps import get_current_user
    from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(router.router)
    app.dependency_overrides[get_current_user] = lambda: ME
    c = TestClient(app)
    limiter.reset()
    try:
        assert c.get("/api/chat-search").status_code == 422
        assert c.get("/api/chat-search", params={"q": "x" * 201}).status_code == 422
        res = c.get("/api/chat-search", params={"q": "   "})
        assert res.status_code == 400 and res.json()["detail"] == "invalid_query"
        res = c.get("/api/chat-search", params={"q": " btc "})
        assert res.status_code == 200
        assert res.json() == {
            "success": True,
            "contacts": [],
            "groups": [],
            "messages": [],
        }
        codes = [
            c.get("/api/chat-search", params={"q": "btc"}).status_code
            for _ in range(30)
        ]
        assert 429 in codes, "30/minute 沒生效"
    finally:
        limiter.reset()


# ── 群組開關 ──────────────────────────────────────────────


async def test_group_flag_off_still_searches_dm(monkeypatch, router):
    async def off(key, default=None, session=None):
        assert key == "group_chat_enabled"
        return False

    monkeypatch.setattr(router.config_repo, "get_config", off)
    res = await _call(router, "btc")
    assert res["success"] is True
    assert router._test_calls == [("me", "btc", False)]


# ── 接真 PG 走一次 endpoint ──────────────────────────────────


async def test_endpoint_end_to_end_flag_off(monkeypatch, gc_pg):
    from api.routers import chat_search as router
    from core.orm.chat_search_repo import chat_search_repo
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    gid = (await group_chat_repo.create_group(u["a"], f"kw群{make.suffix}", session=s))[
        "group"
    ]["id"]
    await group_messages_repo.send_message(gid, u["a"], "kw 群組訊息", session=s)
    dm = (await messages_repo.send_message(u["b"], u["a"], "kw 私訊", session=s))[
        "message"
    ]
    real_search = chat_search_repo.search  # 換掉前先拿真的，綁上測試交易
    monkeypatch.setattr(
        router.chat_search_repo, "search", functools.partial(real_search, session=s)
    )
    flag = {"on": True}

    async def get_config(key, default=None, session=None):
        return flag["on"]

    monkeypatch.setattr(router.config_repo, "get_config", get_config)
    user = {"user_id": u["a"]}

    on = await router.chat_search.__wrapped__(_req(), q=" KW ", current_user=user)
    assert [g["id"] for g in on["groups"]] == [gid]
    assert {m["kind"] for m in on["messages"]} == {"dm", "group"}

    flag["on"] = False
    off = await router.chat_search.__wrapped__(_req(), q="kw", current_user=user)
    assert off["success"] is True and off["groups"] == []
    assert [(m["kind"], m["message_id"]) for m in off["messages"]] == [("dm", dm["id"])]
    assert [c["user_id"] for c in off["contacts"]] == []  # "kw" 不在 b 的名字裡
