"""Admin 會員授予回歸測試。

Bug（2026-08-21 DANNY 回報）：admin 端點傳固定 tx_hash
``admin_grant_<admin_uid>``，upgrade_to_pro 的防重放檢查（同 hash 第二次
→ ValueError「此交易已被處理」）導致同一個 admin **只能成功授予一次** Pro，
之後對任何使用者授予都 400，前端只彈 toast 看起來像「點了沒效」。

修法：tx_hash 每次授予唯一（admin_uid + target_uid + timestamp）。
本測試驗證：兩次授予產生的 tx_hash 不同 → 防重放不再誤傷。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _grant_tx_hash(admin_uid: str, target_uid: str, ts: int) -> str:
    """重現 admin/users.py 的 hash 組法（單一真相由原始碼檢查把關）。"""
    return f"admin_grant_{admin_uid}_{target_uid}_{ts}"


class TestAdminGrantTxHashUnique:
    def test_two_grants_produce_different_hashes(self):
        """同一 admin 對不同目標（或同目標重授）→ hash 必須不同。"""
        admin = "UQadmin123"
        h1 = _grant_tx_hash(admin, "UQuserA", 1787300000)
        h2 = _grant_tx_hash(admin, "UQuserB", 1787300001)
        h3 = _grant_tx_hash(admin, "UQuserA", 1787300500)
        assert len({h1, h2, h3}) == 3, "每次授予的 tx_hash 必須唯一"

    def test_source_uses_unique_hash_not_fixed(self):
        """原始碼把關：不得回到固定 admin_grant_<admin_uid>（回歸防線）。"""
        src = (ROOT / "api/routers/admin/users.py").read_text(encoding="utf-8")
        # 舊 bug 寫法：f"admin_grant_{admin_user['user_id']}"（後面直接接引號收尾）
        assert "admin_grant_{admin_user['user_id']}'" not in src.replace(
            '"', "'"
        ), "admin 授予不得使用固定 tx_hash（會撞防重放）"
        assert "admin_grant_{admin_user['user_id']}_{user_id}" in src.replace(
            '"', "'"
        ), "tx_hash 應含 target uid 保證唯一"

    def test_upgrade_to_pro_dedup_still_active_for_real_payments(self):
        """防重放本身要保留：真實付款同一 tx_hash 二次提交仍要 ValueError。"""
        from core.database import user as user_db

        calls = {"first": True}

        class Cur:
            def execute(self, sql, params=()):
                if "SELECT user_id FROM membership_payments" in sql:
                    if calls["first"]:
                        calls["first"] = False
                        self._row = None
                    else:
                        self._row = ("someone-else",)
                    return self
                if "FROM users WHERE user_id" in sql:
                    self._row = ("free", None, False)
                    return self
                self._row = None
                return self

            def fetchone(self):
                return self._row

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn:
            def cursor(self):
                return Cur()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        with patch.object(user_db, "get_connection", return_value=Conn()):
            with patch("core.database.system_config.get_prices", return_value={}):
                # 第一次：無重複 → 走到 UPDATE（execute 不會炸）
                user_db.upgrade_to_pro("u1", 1, "tx-real-1")
                # 第二次同 hash → ValueError
                with pytest.raises(ValueError, match="已被處理"):
                    user_db.upgrade_to_pro("u1", 1, "tx-real-1")

    def test_endpoint_signature_uncovered(self):
        """端點合約：set_user_membership 存在且掛在 PUT membership 路徑。"""
        from api.routers.admin import users as admin_users

        assert hasattr(admin_users, "set_user_membership")
        src = inspect.getsource(admin_users)
        assert '@router.put("/users/{user_id}/membership")' in src
