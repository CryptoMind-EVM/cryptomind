"""詐騙舉報留言的關卡：Premium（可由 scam_comment_require_pro 關掉）、內容過濾、舉報存在。

跟舉報建立一樣，2026-03-24 的 ORM 遷移（8c11e5e）把 legacy add_scam_comment 換成
scam_tracker_repo.add_comment，repo 註明「Premium／內容／舉報存在由呼叫端處理」，
路由沒接：任何登入者都能留言、內容不過濾，留言到不存在的舉報撞 FK 變 500。
產品文案仍寫「Premium 會員可在此留言」（safety.commentPlaceholder），前端也只對
Premium 顯示留言框。
"""

from __future__ import annotations

import contextlib
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.validators import filter_sensitive_content

pytestmark = pytest.mark.unit

_COMMENTS = "api.routers.scam_tracker.comments"
CLEAN = "I was scammed by this wallet as well."

PREMIUM_USER = {"user_id": "u1", "is_premium": True, "membership_tier": "premium"}
FREE_USER = {"user_id": "u1", "is_premium": False, "membership_tier": "free"}


@pytest.fixture(autouse=True)
def isolate_rate_limits():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    from api.deps import get_current_user
    from api.routers.scam_tracker import router as scam_router

    app = FastAPI()
    app.include_router(scam_router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
    return TestClient(app)


@contextlib.contextmanager
def _env(user=PREMIUM_USER, require_pro=True, add_result=None):
    add = AsyncMock(return_value=add_result or {"success": True, "comment_id": 5})
    get_config = AsyncMock(return_value=require_pro)
    with (
        patch(f"{_COMMENTS}.user_repo.get_by_id", new=AsyncMock(return_value=user)),
        patch(f"{_COMMENTS}.scam_tracker_repo.add_comment", new=add),
        patch(f"{_COMMENTS}.config_repo.get_config", new=get_config),
    ):
        yield add, get_config


def _post(client, content=CLEAN, report_id=3):
    return client.post(
        f"/api/scam-tracker/comments/{report_id}", json={"content": content}
    )


def _assert_clean_detail(resp, reason):
    detail = resp.json()["detail"]
    assert detail["reason"] == reason
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


class TestPremiumGate:
    def test_free_user_gets_403(self, client):
        with _env(user=FREE_USER) as (add, get_config):
            resp = _post(client)
        assert resp.status_code == 403
        _assert_clean_detail(resp, "premium_membership_required")
        add.assert_not_awaited()
        # 與 legacy 同一個開關、同一個預設值
        get_config.assert_awaited_once_with("scam_comment_require_pro", True)

    def test_switch_off_lets_free_user_comment(self, client):
        with _env(user=FREE_USER, require_pro=False) as (add, _):
            resp = _post(client)
        assert resp.status_code == 200
        add.assert_awaited_once()

    def test_premium_user_comments(self, client):
        with _env() as (add, _):
            resp = _post(client)
        assert resp.status_code == 200
        assert resp.json()["comment_id"] == 5
        kwargs = add.await_args.kwargs
        assert kwargs["report_id"] == 3 and kwargs["user_id"] == "u1"


class TestContentFilter:
    @pytest.mark.parametrize(
        "content",
        [
            "Add me on telegram for the refund.",
            "Email me at victim@example.com please",
            "Proof here https://evil.example/x ok",
        ],
    )
    def test_filtered_content_returns_400(self, client, content):
        with _env() as (add, _):
            resp = _post(client, content)
        assert resp.status_code == 400
        detail = _assert_clean_detail(resp, "content_validation_failed")
        assert detail["warnings"]
        add.assert_not_awaited()

    def test_ten_char_comment_is_allowed(self, client):
        """留言的下限是 10 字（CommentCreate、前端 commentMinLength），不是舉報描述的 20。"""
        with _env() as (add, _):
            resp = _post(client, "Same here!")
        assert resp.status_code == 200
        add.assert_awaited_once()

    def test_whitespace_padding_does_not_satisfy_min_length(self, client):
        with _env() as (add, _):
            resp = _post(client, "short" + " " * 10 + "x")
        assert resp.status_code == 400
        add.assert_not_awaited()

    def test_newlines_are_kept_in_stored_content(self, client):
        text = "Line one of proof.\nLine two of proof."
        with _env() as (add, _):
            assert _post(client, text).status_code == 200
        assert add.await_args.kwargs["content"] == text


class TestReportMissing:
    def test_missing_report_is_404_not_500(self, client):
        with _env(add_result={"success": False, "error": "report_not_found"}):
            resp = _post(client, report_id=999)
        assert resp.status_code == 404
        _assert_clean_detail(resp, "report_not_found")


class TestVoteUnaffected:
    def test_free_user_can_still_vote(self, client):
        get_config = AsyncMock()
        with (
            patch(
                "api.routers.scam_tracker.votes.vote_scam_report",
                return_value={"success": True, "action": "voted"},
            ),
            patch(f"{_COMMENTS}.config_repo.get_config", new=get_config),
        ):
            resp = client.post(
                "/api/scam-tracker/votes/3", json={"vote_type": "approve"}
            )
        assert resp.status_code == 200
        get_config.assert_not_awaited()


# ─── Repo：舉報不存在不寫入 ──────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, report_exists):
        self._report_exists = report_exists
        self.added = []
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(3 if self._report_exists else None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            obj.id = 77

    async def refresh(self, obj):
        return None


@pytest.fixture
def fake_session(monkeypatch):
    mod = importlib.import_module("core.orm.scam_tracker_repo")

    def _install(session):
        @contextlib.asynccontextmanager
        async def _using(session_arg=None):
            yield session

        monkeypatch.setattr(mod, "using_session", _using)
        return session

    return _install


class TestRepoAddComment:
    async def test_missing_report_returns_error_without_insert(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_Session(report_exists=False))
        result = await scam_tracker_repo.add_comment(3, "u1", CLEAN)
        assert result == {"success": False, "error": "report_not_found"}
        assert s.added == []

    async def test_existing_report_inserts(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_Session(report_exists=True))
        result = await scam_tracker_repo.add_comment(3, "u1", CLEAN)
        assert result == {"success": True, "comment_id": 77}
        assert len(s.added) == 1


# ─── filter_sensitive_content 的下限 ─────────────────────────────────────────


def test_filter_min_length_defaults_to_20_and_is_overridable():
    assert filter_sensitive_content("Same here!")["valid"] is False  # 舉報描述照舊
    assert filter_sensitive_content("Same here!", min_length=10)["valid"] is True
    assert filter_sensitive_content("too short", min_length=10)["valid"] is False
