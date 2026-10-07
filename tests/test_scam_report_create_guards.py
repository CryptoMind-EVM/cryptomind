"""詐騙舉報建立路徑的三道關卡：Premium、每日上限、內容過濾。

2026-03-24 的 ORM 遷移（8c11e5e）把 POST /api/scam-tracker/reports 從 legacy
core.database.scam_tracker.create_scam_report 換成 scam_tracker_repo.create_report，
repo 的 docstring 寫「驗證由呼叫端處理」，但路由沒接——三道關卡從那天起都沒在跑，
路由裡對應的錯誤分支也成了死碼。產品文案（submit.html／四語 i18n）仍寫「僅 Premium」。
"""

from __future__ import annotations

import contextlib
import importlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit

_REPORTS = "api.routers.scam_tracker.reports"
_REPO = f"{_REPORTS}.scam_tracker_repo"

EQ = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
UQ = "UQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_p0p"
CLEAN = "This wallet sent me a fake airdrop link and drained my USDT balance."

PREMIUM_USER = {"user_id": "u1", "is_premium": True, "membership_tier": "premium"}
FREE_USER = {"user_id": "u1", "is_premium": False, "membership_tier": "free"}


@pytest.fixture(autouse=True)
def isolate_rate_limits():
    # POST 有 5/minute；同一測試 IP 共用計數，不重置第 6 次起全是 429
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


def _post(client, description=CLEAN):
    return client.post(
        "/api/scam-tracker/reports",
        json={
            "scam_wallet_address": UQ,
            "reporter_wallet_address": EQ,
            "scam_type": "phishing",
            "description": description,
        },
    )


@contextlib.contextmanager
def _env(user=PREMIUM_USER, used_today=0, daily_limit=5):
    """路由依賴全部 mock（無 DB）；回傳 (create, count, get_config)。"""
    create = AsyncMock(return_value={"success": True, "report_id": 11})
    count = AsyncMock(return_value=used_today)
    get_config = AsyncMock(return_value=daily_limit)
    with (
        patch(f"{_REPORTS}.user_repo.get_by_id", new=AsyncMock(return_value=user)),
        patch(f"{_REPO}.create_report", new=create),
        patch(f"{_REPO}.count_reports_today", new=count, create=True),
        patch(f"{_REPORTS}.config_repo.get_config", new=get_config),
        # 成功路徑會重算被檢舉錢包的 trust（打鏈上 API）；這裡不測它
        patch(f"{_REPORTS}._recompute_trust_on_scam_hit", new=AsyncMock()),
    ):
        yield create, count, get_config


def _assert_clean_detail(resp, reason):
    detail = resp.json()["detail"]
    # 前端讀 detail.message 顯示；detail.reason 對 i18n key
    assert detail["reason"] == reason
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


# ─── Premium ────────────────────────────────────────────────────────────────


class TestPremiumGate:
    def test_free_user_gets_403_and_nothing_is_written(self, client):
        with _env(user=FREE_USER) as (create, count, _):
            resp = _post(client)
        assert resp.status_code == 403
        _assert_clean_detail(resp, "premium_membership_required")
        create.assert_not_awaited()
        count.assert_not_awaited()

    def test_expired_premium_is_free(self, client):
        """user_repo.get_by_id 已把過期會員的 is_premium 翻成 False；tier 以它為準。"""
        expired = {"user_id": "u1", "is_premium": False, "membership_tier": "free"}
        with _env(user=expired) as (create, _, _):
            resp = _post(client)
        assert resp.status_code == 403
        create.assert_not_awaited()


# ─── 每日上限 ────────────────────────────────────────────────────────────────


class TestDailyLimit:
    def test_at_limit_returns_429_with_counts(self, client):
        with _env(used_today=5, daily_limit=5) as (create, count, get_config):
            resp = _post(client)
        assert resp.status_code == 429
        detail = _assert_clean_detail(resp, "daily_limit_reached")
        assert detail["limit"] == 5 and detail["used"] == 5
        create.assert_not_awaited()
        count.assert_awaited_once_with("u1")
        # 與 legacy 同一個 system_config key、同一個預設值
        get_config.assert_awaited_once_with("scam_report_daily_limit_pro", 5)

    def test_limit_comes_from_system_config(self, client):
        with _env(used_today=2, daily_limit=2) as (create, _, _):
            assert _post(client).status_code == 429
        create.assert_not_awaited()

    def test_under_limit_passes(self, client):
        with _env(used_today=4, daily_limit=5) as (create, _, _):
            resp = _post(client)
        assert resp.status_code == 200
        create.assert_awaited_once()


# ─── 內容過濾 ────────────────────────────────────────────────────────────────


class TestContentFilter:
    @pytest.mark.parametrize(
        "description",
        [
            "Scammer asked me to add him on telegram and then stole my funds.",
            "Contact the scammer at scammer@example.com — he stole my funds.",
            "He sent me to https://evil-airdrop.example/claim and I lost all.",
        ],
    )
    def test_filtered_description_returns_400(self, client, description):
        with _env() as (create, count, _):
            resp = _post(client, description)
        assert resp.status_code == 400
        detail = _assert_clean_detail(resp, "content_validation_failed")
        assert isinstance(detail["warnings"], list) and detail["warnings"]
        create.assert_not_awaited()
        count.assert_not_awaited()

    def test_whitespace_padding_does_not_satisfy_min_length(self, client):
        """legacy 先 sanitize（收空白）再檢查長度——撐到 20 字的空白不算。"""
        with _env() as (create, _, _):
            resp = _post(client, "short" + " " * 30 + "x")
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_newlines_are_kept_in_stored_description(self, client):
        """過濾看收過空白的版本；存的仍是原文（詳情頁把 \\n 轉 <br>）。"""
        text = "First paragraph about the scam.\n\nSecond paragraph with details."
        with _env() as (create, _, _):
            resp = _post(client, text)
        assert resp.status_code == 200
        assert create.await_args.kwargs["description"] == text


# ─── Happy path ─────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_premium_under_limit_clean_content_creates(self, client):
        with _env(used_today=0) as (create, _, _):
            resp = _post(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True and body["report_id"] == 11
        kwargs = create.await_args.kwargs
        assert kwargs["reporter_user_id"] == "u1"
        assert kwargs["scam_wallet_address"] == UQ
        assert EQ not in kwargs["reporter_wallet_masked"]


# ─── 投票／留言不受影響 ──────────────────────────────────────────────────────


class TestVoteAndCommentUnaffected:
    def test_free_user_can_still_vote(self, client):
        """投票本來就開放給所有登入者（設計文件：「登入用戶」）。"""
        count = AsyncMock()
        with (
            patch(
                "api.routers.scam_tracker.votes.vote_scam_report",
                return_value={"success": True, "action": "voted"},
            ),
            patch(f"{_REPO}.count_reports_today", new=count, create=True),
        ):
            resp = client.post(
                "/api/scam-tracker/votes/3", json={"vote_type": "approve"}
            )
        assert resp.status_code == 200
        assert resp.json()["action"] == "voted"
        count.assert_not_awaited()

    # 留言有自己的關卡，見 tests/test_scam_comment_guards.py


# ─── 今日剩餘次數（submit 頁以前寫死 5）──────────────────────────────────────


class TestQuota:
    def _get(self, client, used, limit=5):
        count = AsyncMock(return_value=used)
        get_config = AsyncMock(return_value=limit)
        with (
            patch(f"{_REPO}.count_reports_today", new=count, create=True),
            patch(f"{_REPORTS}.config_repo.get_config", new=get_config),
        ):
            resp = client.get("/api/scam-tracker/reports/quota")
        return resp, count, get_config

    def test_returns_real_remaining(self, client):
        resp, count, get_config = self._get(client, used=2)
        # 也順便證明 /quota 沒被 /{report_id} 吃掉（那條是 int，會 422）
        assert resp.status_code == 200
        body = resp.json()
        assert (body["limit"], body["used"], body["remaining"]) == (5, 2, 3)
        count.assert_awaited_once_with("u1")
        get_config.assert_awaited_once_with("scam_report_daily_limit_pro", 5)

    def test_remaining_never_negative(self, client):
        resp, _, _ = self._get(client, used=7, limit=5)
        assert resp.json()["remaining"] == 0

    def test_requires_login(self):
        from api.deps import get_current_user
        from api.routers.scam_tracker.reports import router

        route = next(r for r in router.routes if r.path.endswith("/quota"))
        assert get_current_user in [d.call for d in route.dependant.dependencies]


# ─── Repo：今日舉報數 ────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Session:
    def __init__(self, value):
        self._value = value
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._value)


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


class TestCountReportsToday:
    async def test_counts_this_reporter_since_utc_midnight(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_Session(3))
        assert await scam_tracker_repo.count_reports_today("u1") == 3

        stmt = s.statements[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "count(" in sql.lower()
        assert "scam_reports.reporter_user_id" in sql
        assert "scam_reports.created_at >=" in sql
        params = list(stmt.compile(dialect=postgresql.dialect()).params.values())
        assert "u1" in params
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        assert midnight in params

    async def test_none_count_is_zero(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        fake_session(_Session(None))
        assert await scam_tracker_repo.count_reports_today("u1") == 0


# ─── 前端：reason → 在地化訊息 ───────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_submit_error_keys_exist(lang):
    """test_i18n_coverage 只掃 web/js，scam-tracker/js 用到的 key 要自己守。"""
    safety = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )["safety"]
    for key in (
        "proRequired",
        "proRequiredDesc",
        "dailyLimitReached",
        "contentRejected",
        "reportNotExist",
        "dailyQuota",
        "quotaLoadFailed",
    ):
        assert safety.get(key), f"{lang} 缺 safety.{key}"
    assert "{{limit}}" in safety["dailyLimitReached"]
    assert "{{count}}" in safety["dailyQuota"]


def test_ru_membership_copy_says_premium_and_reporting():
    """ru 以前把 Premium 寫成 Pro、把「檢舉須知」寫成「投票需要 Pro」，跟 zh-TW 意思相反。"""
    safety = json.loads(
        (REPO / "web" / "js" / "i18n" / "ru.json").read_text(encoding="utf-8")
    )["safety"]
    shown = (
        "proRequired",
        "upgradePro",
        "proNoticeTitle",
        "proNoticeDesc",
        "warningDesc",
        "voteHint",
    )
    for key in shown:
        assert "Pro" not in safety[key].replace("Premium", ""), (key, safety[key])
        assert "голос" not in safety[key].lower() or key == "voteHint", (
            key,
            safety[key],
        )
    assert "Premium" in safety["proNoticeTitle"]
    assert "Premium" in safety["proNoticeDesc"]
    # 投票開放給所有登入者；提示講的是自動驗證門檻（10 票、70%）
    assert "10" in safety["voteHint"] and "70" in safety["voteHint"]


def test_submit_page_has_no_hardcoded_quota():
    html = (REPO / "web" / "scam-tracker" / "submit.html").read_text(encoding="utf-8")
    assert "remaining today: 5" not in html
    assert 'id="remaining-quota"' not in html
    js = (REPO / "web" / "scam-tracker" / "js" / "scam-tracker-i18n.js").read_text(
        encoding="utf-8"
    )
    assert "count: '5'" not in js


def test_submit_report_localizes_guard_errors_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "scam_submit_errors.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "scam_submit_errors: ok" in proc.stderr
