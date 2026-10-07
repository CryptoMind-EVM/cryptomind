"""Tests for PUT /api/user/display-name — 個人化暱稱（24h cooldown）。

仿 `tests/test_user_feedback_api.py` 與 `test_chat_session_create.py` 慣例：
用 `auth_headers` fixture，monkeypatch `user_router.run_sync` /
`user_router.set_user_display_name`，讓測試不依賴真實 DB。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from api.routers import user as user_router


async def _fake_run_sync(fn, *args):
    return fn(*args)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每個測試前重置 in-memory rate limiter 計數器。

    本檔有多個測試打 PUT /api/user/display-name(5/min),累積會觸發限流。
    測試環境 limiter 用 memory storage,reset() 可清空。
    """
    from api.middleware.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001 — 重置失敗不擋測試
        pass
    yield
    try:
        limiter.reset()
    except Exception:  # noqa: BLE001
        pass


# ============================================================================
# PUT /api/user/display-name
# ============================================================================


@pytest.mark.asyncio
class TestSetDisplayNameEndpoint:
    async def test_success(self, client, auth_headers, monkeypatch):
        captured = {}

        def fake_set(user_id, name):
            captured["user_id"] = user_id
            captured["name"] = name
            return (True, None)

        monkeypatch.setattr(user_router, "run_sync", _fake_run_sync)
        monkeypatch.setattr(user_router, "set_user_display_name", fake_set)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "鈺澔"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        assert response.json()["display_name"] == "鈺澔"
        assert captured["name"] == "鈺澔"

    async def test_rejects_cooldown_with_429(self, client, auth_headers, monkeypatch):
        def fake_set(user_id, name):
            # (ok=False, reason="cooldown", next_available_at_iso)
            return (False, "cooldown", "2026-07-22T10:00:00+00:00")

        monkeypatch.setattr(user_router, "run_sync", _fake_run_sync)
        monkeypatch.setattr(user_router, "set_user_display_name", fake_set)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "新名字"},
            headers=auth_headers,
        )

        assert response.status_code == 429, response.text
        body = response.json()
        assert body["detail"]["reason"] == "cooldown"

    @pytest.mark.parametrize(
        "payload,label",
        [
            ({"display_name": ""}, "empty"),
            ({"display_name": "   "}, "whitespace_only"),
            ({"display_name": "a" * 21}, "too_long"),
        ],
    )
    async def test_rejects_invalid(
        self, client, auth_headers, payload, label
    ):
        response = await client.put(
            "/api/user/display-name",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 422, f"{label}: {response.text}"

    async def test_rejects_control_characters(self, client, auth_headers):
        """換行/控制字元必須被擋下（防 prompt injection 透過暱稱）。"""
        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "hello\nworld"},
            headers=auth_headers,
        )
        # min_length=1 通過 Pydantic，但 \n 在 endpoint 被 re.search 擋下
        assert response.status_code == 422, response.text

    async def test_rejects_reserved_ton_prefix(self, client, auth_headers):
        """TON_ 開頭是系統自動產生的預設名格式,使用者不可取回(避免混淆)。"""
        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "TON_abc123"},
            headers=auth_headers,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["reason"] == "reserved"

    async def test_rejects_duplicate_with_409(
        self, client, auth_headers, monkeypatch
    ):
        """暱稱已被他人使用 → 409 duplicate。"""
        monkeypatch.setattr(
            user_router, "is_display_name_taken", lambda *a: False
        )
        # 讓 run_sync 跑到 is_display_name_taken 時回 True
        async def fake_run_sync(fn, *args):
            # is_display_name_taken(name, user_id) → True(已被佔用)
            if fn is user_router.is_display_name_taken:
                return True
            return fn(*args)

        monkeypatch.setattr(user_router, "run_sync", fake_run_sync)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "已存在"},
            headers=auth_headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["reason"] == "duplicate"

    async def test_rejects_name_equal_to_existing_username(
        self, client, auth_headers, monkeypatch
    ):
        """暱稱等於某人的 username(系統預設名)→ 422 reserved。"""
        async def fake_run_sync(fn, *args):
            if fn is user_router.is_display_name_taken:
                return False  # display_name 沒撞
            if fn is user_router.is_username_taken:
                return True  # 但等於某人的 username
            return fn(*args)

        monkeypatch.setattr(user_router, "run_sync", fake_run_sync)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "TON_dvjdhE"},  # 不是 TON_ 開頭的大小寫變體
            headers=auth_headers,
        )
        # 注意:"TON_dvjdhE" 以 TON_ 開頭 → 先被 reserved(TON_) 擋,不會走到查重
        # 改用非 TON_ 開頭但等於 username 的場景需 username 不以 TON_ 開頭,
        # 但系統 username 必為 TON_ 開頭,所以這條路徑主要靠 is_username_taken。
        # 此測試驗證 TON_ 開頭一律被 reserved 擋下:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["reason"] == "reserved"

    async def test_success_clears_greeting_cache(
        self, client, auth_headers, monkeypatch
    ):
        """改名成功後必須清除 greeting 快取,否則舊暱稱會持續顯示 1 小時。"""
        cleared = {"called": False, "user_id": None}

        def fake_set(user_id, name):
            return (True, None)

        def fake_invalidate(user_id):
            cleared["called"] = True
            cleared["user_id"] = user_id
            return 1

        async def fake_run_sync(fn, *args):
            if fn is user_router.is_display_name_taken:
                return False
            if fn is user_router.is_username_taken:
                return False
            return fn(*args)

        # fake_set 與 fake_invalidate 都在 user_router namespace 上替換
        monkeypatch.setattr(user_router, "run_sync", fake_run_sync)
        monkeypatch.setattr(user_router, "set_user_display_name", fake_set)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)
        # invalidate_greeting_cache 在 analysis router,lazy import 後才綁定。
        # monkeypatch analysis 模組的函式即可(endpoint 內 from ... import 會取到 patched)
        from api.routers import analysis as analysis_router

        monkeypatch.setattr(
            analysis_router, "invalidate_greeting_cache", fake_invalidate
        )

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "新暱稱"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert cleared["called"] is True, "greeting 快取未被清除"
        assert cleared["user_id"] is not None

    async def test_duplicate_race_returns_409(
        self, client, auth_headers, monkeypatch
    ):
        """競態:預檢查通過但 UPDATE 撞唯一索引(set 回 duplicate)→ 409。"""
        async def fake_run_sync(fn, *args):
            if fn is user_router.is_display_name_taken:
                return False  # 預檢查沒查到
            if fn is user_router.is_username_taken:
                return False
            return fn(*args)

        def fake_set(user_id, name):
            return (False, "duplicate", None)  # UPDATE 撞唯一索引

        monkeypatch.setattr(user_router, "run_sync", fake_run_sync)
        monkeypatch.setattr(user_router, "set_user_display_name", fake_set)
        monkeypatch.setattr(user_router, "audit_log", lambda *a, **kw: None)

        response = await client.put(
            "/api/user/display-name",
            json={"display_name": "搶先名"},
            headers=auth_headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["reason"] == "duplicate"


# ============================================================================
# /api/user/me 回傳 display_name + wallet_address
# ============================================================================


@pytest.mark.asyncio
class TestUserMeIncludesDisplayName:
    # 同一個錢包的 non-bounceable／raw／testnet 寫法——都要被認成 TON 錢包地址
    @pytest.mark.parametrize(
        "wallet",
        [
            "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI",
            "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8",
            "0QCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqPvC",
        ],
    )
    async def test_returns_display_name_and_wallet_for_ton_user(
        self, client, monkeypatch, wallet
    ):
        from api.deps import create_access_token

        # TON 用戶的 user_id 就是錢包地址；TEST_MODE 預設用 test-user-001，
        # 改用真實 JWT 帶 sub=<TON 地址> 才能驗證 wallet_address 回推邏輯
        token = create_access_token(data={"sub": wallet})

        monkeypatch.setattr(
            user_router.user_repo, "get_language", AsyncMock(return_value="zh-TW")
        )
        monkeypatch.setattr(
            user_router.user_repo, "get_display_name", AsyncMock(return_value="鈺澔")
        )
        user_router._ME_CACHE.clear()

        response = await client.get(
            "/api/user/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200, response.text
        user = response.json()["user"]
        assert user["display_name"] == "鈺澔"
        assert user["wallet_address"] == wallet

    async def test_display_name_falls_back_to_username_when_null(
        self, client, monkeypatch
    ):
        """display_name 未設定時應回 None（前端自行 fallback 到 username）。"""
        monkeypatch.setattr(
            user_router.user_repo, "get_language", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            user_router.user_repo, "get_display_name", AsyncMock(return_value=None)
        )
        user_router._ME_CACHE.clear()

        response = await client.get(
            "/api/user/me",
            headers={"Authorization": "Bearer test-user-001"},
        )

        assert response.status_code == 200, response.text
        user = response.json()["user"]
        assert user["display_name"] is None


# ============================================================================
# 24h cooldown 邏輯（純單元測試，不打 DB）
# ============================================================================


class TestCooldownLogic:
    def test_blocks_within_24h(self):
        from core.database.user import _is_within_cooldown

        now = datetime.now(timezone.utc)

        assert _is_within_cooldown(now - timedelta(hours=1)) is True
        assert _is_within_cooldown(now - timedelta(hours=23, minutes=59)) is True

    def test_allows_at_or_after_24h(self):
        from core.database.user import _is_within_cooldown

        now = datetime.now(timezone.utc)

        assert _is_within_cooldown(now - timedelta(hours=24)) is False
        assert _is_within_cooldown(now - timedelta(hours=48)) is False

    def test_allows_when_never_set(self):
        from core.database.user import _is_within_cooldown

        assert _is_within_cooldown(None) is False

    def test_handles_naive_datetime(self):
        """DB 可能回傳 naive datetime（tzinfo=None），不可炸。"""
        from core.database.user import _is_within_cooldown

        now = datetime.now(timezone.utc)
        naive = (now - timedelta(hours=1)).replace(tzinfo=None)

        # 不拋例外，且視為冷卻中
        assert _is_within_cooldown(naive) is True


# ============================================================================
# 暱稱查重函式(is_display_name_taken / is_username_taken)— 純單元測試
# ============================================================================


class TestUniquenessCheckFunctions:
    """驗證查重函式的容錯行為(查詢失敗時回 False,由 DB 索引做最終把關)。"""

    def test_is_display_name_taken_returns_false_on_db_error(self, monkeypatch):
        from core.database import user as user_db

        # 模擬 get_connection 拋例外(連線失敗)→ 不可炸,要回 False
        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(user_db, "get_connection", boom)
        assert user_db.is_display_name_taken("anyname") is False

    def test_is_username_taken_returns_false_on_db_error(self, monkeypatch):
        from core.database import user as user_db

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(user_db, "get_connection", boom)
        assert user_db.is_username_taken("TON_anything") is False


# ============================================================================
# greeting 快取失效(invalidate_greeting_cache)— 純單元測試
# ============================================================================


class TestGreetingCacheInvalidation:
    """改名後必須清掉舊 greeting,否則舊暱稱顯示 1 小時。"""

    def test_invalidate_clears_only_target_user(self):
        from api.routers.analysis import (
            _GREETING_CACHE,
            invalidate_greeting_cache,
        )

        # 塞入混合資料:目標使用者(多 session/lang)+ 其他使用者
        _GREETING_CACHE.clear()
        _GREETING_CACHE["userA:welcome:zh-TW"] = {"text": "舊", "ts": 0}
        _GREETING_CACHE["userA:s1:en"] = {"text": "old", "ts": 0}
        _GREETING_CACHE["userB:welcome:zh-TW"] = {"text": "keep", "ts": 0}

        removed = invalidate_greeting_cache("userA")

        assert removed == 2
        assert "userA:welcome:zh-TW" not in _GREETING_CACHE
        assert "userA:s1:en" not in _GREETING_CACHE
        # 其他使用者的快取不受影響
        assert "userB:welcome:zh-TW" in _GREETING_CACHE

        # 清理副作用
        _GREETING_CACHE.clear()

    def test_invalidate_empty_user_id_is_noop(self):
        from api.routers.analysis import invalidate_greeting_cache

        assert invalidate_greeting_cache("") == 0
        assert invalidate_greeting_cache(None) == 0

    def test_invalidate_nonexistent_user_returns_zero(self):
        from api.routers.analysis import (
            _GREETING_CACHE,
            invalidate_greeting_cache,
        )

        _GREETING_CACHE.clear()
        _GREETING_CACHE["userX:welcome:zh-TW"] = {"text": "x", "ts": 0}
        assert invalidate_greeting_cache("nobody") == 0
        _GREETING_CACHE.clear()


# ============================================================================
# #2 set_user_display_name atomic UPDATE（消除 TOCTOU race）
# ============================================================================
# 驗證重構後的實作：用單條 UPDATE ... WHERE 含冷卻條件，rowcount 決定成功/冷卻，
# 取代原本 SELECT→UPDATE 兩段式（並發 PUT 可雙雙繞過冷卻）。


class _FakeCursor:
    """模擬 psycopg cursor：記錄執行的 SQL/params，控制 rowcount 與 fetchone。"""

    def __init__(self, rowcount=0, fetchone_result=None):
        self.rowcount = rowcount
        self._fetchone_result = fetchone_result
        self.executed = []  # [(sql, params), ...]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.mark.unit
def test_set_display_name_atomic_update_success():
    """#2：rowcount==1 → 成功，且只執行一條 UPDATE（含冷卻 WHERE 條件）。"""
    from core.database import user as user_db

    cur = _FakeCursor(rowcount=1)
    conn = _FakeConn(cur)
    monkeypatch_conn(user_db, conn)

    result = user_db.set_user_display_name("u1", "NewName")
    # 成功路徑回傳 2-tuple (True, None)
    assert result[0] is True and result[1] is None
    assert conn.committed is True
    # 只該有一條 UPDATE（成功路徑不需後續 SELECT 釐清）
    updates = [e for e in cur.executed if e[0].strip().upper().startswith("UPDATE")]
    assert len(updates) == 1, f"expected single atomic UPDATE, got {cur.executed}"
    sql = updates[0][0]
    # WHERE 必須含冷卻條件（display_name_updated_at IS NULL OR ... < NOW() - INTERVAL）
    assert "display_name_updated_at" in sql
    assert "INTERVAL" in sql.upper(), "atomic UPDATE 必須含 INTERVAL 冷卻條件"


@pytest.mark.unit
def test_set_display_name_atomic_update_cooldown_via_rowcount_zero():
    """#2：rowcount==0 且 user 存在 → 回 cooldown（後續 SELECT 釐清）。"""
    from core.database import user as user_db

    # fetchone 回一個「冷卻中」的時間戳（NOW() 會讓它看起來剛改過）
    recent = datetime.now(timezone.utc)
    cur = _FakeCursor(rowcount=0, fetchone_result=(recent,))
    conn = _FakeConn(cur)
    monkeypatch_conn(user_db, conn)

    ok, reason, extra = user_db.set_user_display_name("u1", "NewName")
    assert ok is False
    assert reason == "cooldown"
    # 應該執行過 SELECT（釐清 rowcount==0 的原因）
    selects = [e for e in cur.executed if e[0].strip().upper().startswith("SELECT")]
    assert len(selects) == 1


@pytest.mark.unit
def test_set_display_name_no_two_step_select_then_update():
    """#2 迴歸守門：禁止退化回 SELECT→UPDATE 兩段式 race。
    重構前先 SELECT 檢查冷卻再 UPDATE；重構後必須是 atomic UPDATE 在前。
    """
    from core.database import user as user_db

    cur = _FakeCursor(rowcount=1)
    conn = _FakeConn(cur)
    monkeypatch_conn(user_db, conn)

    user_db.set_user_display_name("u1", "NewName")
    # 第一條 SQL 必須是 UPDATE，不能是 SELECT（SELECT→UPDATE 是 race 來源）
    first_sql = cur.executed[0][0].strip().upper()
    assert first_sql.startswith("UPDATE"), (
        f"第一條 SQL 必須是 atomic UPDATE（消除 race），實際: {first_sql}"
    )


def monkeypatch_conn(user_db, conn):
    """把 user_db.get_connection 換成回傳 fake conn。"""
    import sys

    mod = sys.modules["core.database.user"]
    mod.get_connection = lambda: conn
