"""AI 回答快照分享 API（api/routers/answer_share.py，2026-10-05 任務 D）。

用記憶體假 repo ＋ 假的對話查詢：這裡驗的是 API 的安全邊界——旗標關閉全 404、沒登入 401、
只能分享自己的對話、內容由伺服器取且已遮蔽、token 一次性出現、撤銷／過期後 404、公開頁不能被 XSS。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import answer_share as core

pytestmark = pytest.mark.unit

EVM = "0x" + "ab12" * 10
FLAG = "CONVERSATION_SHARE_ENABLED"
UID, OTHER = "u-alice", "u-bob"

# session -> (擁有者, [(role, content)])
CHATS = {
    "s-alice": (
        UID,
        [
            ("user", "BTC 現在怎麼看"),
            (
                "assistant",
                f"整理中。你的地址 {EVM} 持有 BTC。**以上為資料分析，非投資建議。**",
            ),
        ],
    ),
    "s-bob": (OTHER, [("user", "ETH 呢"), ("assistant", "ETH 整理中")]),
}


class FakeRepo:
    def __init__(self):
        self.rows: list[dict] = []

    async def create(
        self,
        user_id,
        question,
        answer,
        *,
        now=None,
        session=None,
        daily_limit=None,
        max_active=None,
    ):
        from core.orm.answer_share_repo import ShareLimitError

        if daily_limit is not None and await self.count_recent(user_id) >= daily_limit:
            raise ShareLimitError("daily")
        if (
            max_active is not None
            and sum(
                1 for r in self.rows if r["user_id"] == user_id and not r["revoked"]
            )
            >= max_active
        ):
            raise ShareLimitError("active")
        now = now or datetime.now(timezone.utc)
        token = core.new_token()
        row = {
            "id": len(self.rows) + 1,
            "hash": core.hash_token(token),
            "user_id": user_id,
            "question": question,
            "answer": answer,
            "created_at": now,
            "expires_at": now + timedelta(days=core.TTL_DAYS),
            "revoked": False,
        }
        self.rows.append(row)
        return {"id": row["id"], "token": token, "expires_at": row["expires_at"]}

    async def get_active(self, token, *, now=None, session=None):
        now = now or datetime.now(timezone.utc)
        if not core.valid_token_shape(token):
            self.malformed_lookups = getattr(self, "malformed_lookups", 0) + 0
            return None
        for r in self.rows:
            if (
                r["hash"] == core.hash_token(token)
                and not r["revoked"]
                and r["expires_at"] > now
            ):
                return {
                    "question": r["question"],
                    "answer": r["answer"],
                    "created_at": r["created_at"],
                }
        return None

    async def list_active(self, user_id, *, now=None, session=None):
        return [
            {
                "id": r["id"],
                "question": r["question"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in reversed(self.rows)
            if r["user_id"] == user_id and not r["revoked"]
        ]

    async def revoke(self, user_id, share_id, *, now=None, session=None):
        for r in self.rows:
            if r["id"] == share_id and r["user_id"] == user_id and not r["revoked"]:
                r["revoked"] = True
                return True
        return False

    async def count_recent(self, user_id, *, now=None, session=None):
        return sum(1 for r in self.rows if r["user_id"] == user_id)

    async def purge_expired(self, *, now=None, session=None):
        return 0


@pytest.fixture
def api(monkeypatch):
    from api.deps import get_current_user
    from api.middleware.rate_limit import limiter
    from api.routers import answer_share as mod

    limiter.reset()  # slowapi 的計數是全域的，不重設的話後面的測試會吃到 429
    repo = FakeRepo()
    monkeypatch.setattr(mod, "answer_share_repo", repo)
    monkeypatch.setattr(
        mod,
        "check_session_ownership",
        lambda sid, uid: CHATS.get(sid, (None,))[0] == uid,
    )

    def fake_find_turn(sid, question):
        from core.database.chat import find_turn  # noqa: F401  確認 import 路徑存在

        chat = CHATS.get(sid)
        if not chat:
            return None
        rows = chat[1]
        for i in range(len(rows) - 1, -1, -1):
            if rows[i][0] == "user" and core.clean_question(
                rows[i][1]
            ) == core.clean_question(question):
                for role, content in rows[i + 1 :]:
                    if role == "assistant":
                        return {"question": rows[i][1], "answer": content}
        return None

    monkeypatch.setattr(mod, "find_turn", fake_find_turn)
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.delenv("ANSWER_SHARE_DAILY_LIMIT", raising=False)

    app = FastAPI()
    app.include_router(mod.router)
    state = {"user": UID}
    app.dependency_overrides[get_current_user] = lambda: {"user_id": state["user"]}
    client = TestClient(app)
    client.repo, client.state, client.app_ = repo, state, app
    return client


def _body(session="s-alice", question="BTC 現在怎麼看"):
    return {"session_id": session, "question": question}


# ── 旗標 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "false", "0", ""])
def test_everything_is_404_when_the_flag_is_off(api, monkeypatch, raw):
    made = api.post("/api/share/answers", json=_body()).json()  # 先在開啟時建一份
    token = made["url"].rsplit("/", 1)[1]
    if raw is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, raw)
    assert api.post("/api/share/answers/preview", json=_body()).status_code == 404
    assert api.post("/api/share/answers", json=_body()).status_code == 404
    assert api.get("/api/share/answers").status_code == 404
    assert api.delete("/api/share/answers/1").status_code == 404
    assert api.get(f"/api/public/share/answers/{token}").status_code == 404, (
        "已建立的連結在旗標關閉時也打不開"
    )
    assert api.get(f"/s/{token}").status_code == 404


def test_default_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    from core.feature_flags import answer_share_enabled

    assert answer_share_enabled() is False


# ── 授權 ─────────────────────────────────────────────────────────────────


def test_write_endpoints_require_login_and_public_ones_do_not():
    """CI 開著 TEST_MODE（get_current_user 會放行測試帳號），所以不靠實際打 401，直接檢查路由相依。"""
    from api.deps import get_current_user
    from api.routers import answer_share as mod

    needs_login = {}
    for route in mod.router.routes:
        deps = {d.call for d in route.dependant.dependencies}
        needs_login[route.path] = get_current_user in deps
    assert needs_login == {
        "/api/share/answers/preview": True,
        "/api/share/answers": True,  # POST 與 GET 同一個 path
        "/api/share/answers/{share_id}": True,
        "/api/public/share/answers/{token}": False,
        "/s/{token}": False,
    }


def test_every_route_has_a_rate_limit():
    import inspect

    from api.routers import answer_share as mod

    src = inspect.getsource(mod)
    assert src.count("@limiter.limit(") == src.count("@router.")


def test_cannot_share_someone_elses_conversation(api):
    assert (
        api.post(
            "/api/share/answers/preview", json=_body("s-bob", "ETH 呢")
        ).status_code
        == 404
    )
    assert (
        api.post("/api/share/answers", json=_body("s-bob", "ETH 呢")).status_code == 404
    )
    assert api.post("/api/share/answers", json=_body("s-nope", "x")).status_code == 404
    assert api.repo.rows == []


def test_unknown_question_in_my_own_session_is_404(api):
    assert (
        api.post("/api/share/answers", json=_body(question="我沒問過這句")).status_code
        == 404
    )


# ── 預覽／建立 ───────────────────────────────────────────────────────────


def test_preview_is_redacted_and_writes_nothing(api):
    r = api.post("/api/share/answers/preview", json=_body())
    assert r.status_code == 200
    d = r.json()
    assert EVM not in d["answer"] and "[address]" in d["answer"]
    assert d["redactions"] == 1 and d["ttl_days"] == core.TTL_DAYS
    assert api.repo.rows == []


def test_create_uses_server_side_answer_not_client_text(api):
    body = {**_body(), "answer": "我自己編的答案", "user_id": OTHER}
    r = api.post("/api/share/answers", json=body)
    assert r.status_code == 200
    stored = api.repo.rows[0]
    assert "我自己編的答案" not in stored["answer"] and stored["user_id"] == UID
    assert EVM not in stored["answer"] and "[address]" in stored["answer"]


def test_create_returns_the_link_once_and_only_the_hash_is_stored(api):
    d = api.post("/api/share/answers", json=_body()).json()
    token = d["url"].rsplit("/s/", 1)[1]
    assert core.valid_token_shape(token) and d["url"].endswith(f"/s/{token}")
    assert token not in repr(api.repo.rows), "資料庫（假 repo）裡不能有 token 本身"
    assert api.get("/api/share/answers").json()["items"][0].keys() == {
        "id",
        "question",
        "created_at",
        "expires_at",
    }
    assert token not in api.get("/api/share/answers").text


def test_daily_limit(api, monkeypatch):
    monkeypatch.setenv("ANSWER_SHARE_DAILY_LIMIT", "2")
    assert api.post("/api/share/answers", json=_body()).status_code == 200
    assert api.post("/api/share/answers", json=_body()).status_code == 200
    assert api.post("/api/share/answers", json=_body()).status_code == 429
    monkeypatch.setenv("ANSWER_SHARE_DAILY_LIMIT", "oops")  # 壞值退回預設，不會炸
    assert api.post("/api/share/answers", json=_body()).status_code == 200


# ── 撤銷 ─────────────────────────────────────────────────────────────────


def test_revoke_then_404_and_only_the_owner_can_revoke(api):
    d = api.post("/api/share/answers", json=_body()).json()
    token = d["url"].rsplit("/", 1)[1]
    assert api.get(f"/api/public/share/answers/{token}").status_code == 200
    api.state["user"] = OTHER
    assert api.delete(f"/api/share/answers/{d['id']}").status_code == 404, (
        "別人撤不掉，也看不出存在"
    )
    assert api.get(f"/api/public/share/answers/{token}").status_code == 200
    api.state["user"] = UID
    assert api.delete(f"/api/share/answers/{d['id']}").status_code == 200
    assert api.get(f"/api/public/share/answers/{token}").status_code == 404
    assert api.get(f"/s/{token}").status_code == 404
    assert api.delete(f"/api/share/answers/{d['id']}").status_code == 404, "重複撤銷"


# ── 公開讀取 ─────────────────────────────────────────────────────────────


def test_public_json_has_no_user_info_and_is_not_cacheable_or_indexable(api):
    token = api.post("/api/share/answers", json=_body()).json()["url"].rsplit("/", 1)[1]
    r = api.get(f"/api/public/share/answers/{token}")
    assert r.status_code == 200
    assert set(r.json()) == {"success", "question", "answer", "created_at"}
    assert UID not in r.text
    assert (
        r.headers["cache-control"] == "no-store"
        and r.headers["x-robots-tag"] == "noindex"
    )


@pytest.mark.parametrize("bad", ["short", "x" * 33, "a" * 32, "<script>", "%00" * 11])
def test_unknown_or_malformed_token_is_404_with_noindex(api, bad):
    r = api.get(f"/api/public/share/answers/{bad}")
    assert r.status_code == 404 and r.headers["x-robots-tag"] == "noindex"
    page = api.get(f"/s/{bad}")
    assert page.status_code == 404 and 'name="robots" content="noindex"' in page.text
    assert "og:title" not in page.text


def test_expired_link_is_404(api):
    token = api.post("/api/share/answers", json=_body()).json()["url"].rsplit("/", 1)[1]
    api.repo.rows[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert api.get(f"/api/public/share/answers/{token}").status_code == 404


# ── 公開頁（連結預覽）────────────────────────────────────────────────────


def test_share_page_has_og_preview_and_noindex(api):
    token = api.post("/api/share/answers", json=_body()).json()["url"].rsplit("/", 1)[1]
    r = api.get(f"/s/{token}")
    assert r.status_code == 200
    html = r.text
    assert 'name="robots" content="noindex"' in html
    title = re.search(r'og:title" content="([^"]*)"', html).group(1)
    assert "BTC 現在怎麼看" in title
    desc = re.search(r'og:description" content="([^"]*)"', html).group(1)
    assert desc.startswith("整理中") and len(desc) <= core.OG_DESCRIPTION_LEN + 1
    assert EVM not in html and "**" not in desc
    assert (
        r.headers["x-robots-tag"] == "noindex"
        and r.headers["cache-control"] == "no-store"
    )
    assert "<!--OG-->" not in html


def test_share_page_cannot_be_xss_ed_through_the_question(api, monkeypatch):
    evil = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
    CHATS["s-evil"] = (
        UID,
        [("user", evil), ("assistant", "<script>alert(3)</script> 答案")],
    )
    try:
        token = (
            api.post("/api/share/answers", json=_body("s-evil", evil))
            .json()["url"]
            .rsplit("/", 1)[1]
        )
    finally:
        del CHATS["s-evil"]
    html = api.get(f"/s/{token}").text
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    # 頁面腳本只用 textContent 放內容
    js = open("web/answer-share.js", encoding="utf-8").read()
    assert (
        "innerHTML" not in js and "outerHTML" not in js and "document.write" not in js
    )


def test_template_has_the_og_placeholder_and_no_inline_script():
    page = open("web/answer-share.html", encoding="utf-8").read()
    assert page.count("<!--OG-->") == 1
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page), "CSP 禁 inline script"
    assert not re.search(r"\son\w+=", page), "CSP 禁 inline handler"


# ── 資安審查補的 ──────────────────────────────────────────────────────────


def test_flag_off_is_404_even_without_login_or_with_a_bad_body(api, monkeypatch):
    """旗標關閉時，未登入不能回 401、body 錯不能回 422：那會暴露這些路由存在。"""
    from fastapi import HTTPException

    from api.deps import get_current_user

    def unauthenticated():
        raise HTTPException(status_code=401, detail="Unauthorized")

    api.app_.dependency_overrides[get_current_user] = unauthenticated
    monkeypatch.setenv(FLAG, "false")
    assert api.post("/api/share/answers", json=_body()).status_code == 404
    assert api.post("/api/share/answers", json={"nope": 1}).status_code == 404
    assert api.get("/api/share/answers").status_code == 404
    assert api.delete("/api/share/answers/1").status_code == 404
    monkeypatch.setenv(FLAG, "true")  # 開啟後才輪到登入檢查
    assert api.post("/api/share/answers", json=_body()).status_code == 401


def test_active_link_cap_is_409_and_revoking_frees_a_slot(api, monkeypatch):
    monkeypatch.setattr(core, "MAX_ACTIVE", 2)
    first = api.post("/api/share/answers", json=_body()).json()
    assert api.post("/api/share/answers", json=_body()).status_code == 200
    r = api.post("/api/share/answers", json=_body())
    assert r.status_code == 409 and "active" in r.json()["detail"].lower()
    assert api.delete(f"/api/share/answers/{first['id']}").status_code == 200
    assert api.post("/api/share/answers", json=_body()).status_code == 200


@pytest.mark.parametrize(
    "bad_id", ["0", "-1", str(2**63), "99999999999999999999999", "abc"]
)
def test_malformed_share_id_is_rejected_not_a_500(api, bad_id):
    assert api.delete(f"/api/share/answers/{bad_id}").status_code == 422


def test_nul_bytes_in_the_body_are_rejected_not_a_500(api):
    assert (
        api.post("/api/share/answers", json=_body(session="s-alice\x00x")).status_code
        == 422
    )
    assert (
        api.post(
            "/api/share/answers/preview", json=_body(question="BTC\x00")
        ).status_code
        == 422
    )


def test_the_expensive_redaction_runs_off_the_event_loop():
    import inspect

    from api.routers import answer_share as mod

    src = inspect.getsource(mod._snapshot)
    assert "run_sync(core.prepare_snapshot" in src


def test_og_preview_never_contains_urls_even_when_the_answer_does(api):
    CHATS["s-url"] = (
        UID,
        [
            ("user", "看 https://evil.example 這個"),
            ("assistant", "領獎 https://evil.example/login?x=1 快"),
        ],
    )
    try:
        token = (
            api.post(
                "/api/share/answers",
                json=_body("s-url", "看 https://evil.example 這個"),
            )
            .json()["url"]
            .rsplit("/", 1)[1]
        )
    finally:
        del CHATS["s-url"]
    html = api.get(f"/s/{token}").text
    head = html.split("</head>")[0]
    assert "evil.example" not in head and "[link]" in head


def test_token_is_masked_in_the_audit_log_path():
    from api.middleware.audit import _loggable_path

    t = "A" * 32
    assert _loggable_path(f"/s/{t}") == "/s/***"
    assert (
        _loggable_path(f"/api/public/share/answers/{t}")
        == "/api/public/share/answers/***"
    )
    assert _loggable_path("/api/share/answers/7") == "/api/share/answers/7"
