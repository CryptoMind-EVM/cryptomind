"""cron_wallet_monitor entitlement gate 單元測試（design 2026-08-13 §9.7）。

只測純函式 ``_entitlement_filter``；不打 DB、不抓 TonAPI。
fake resolver 注入 Entitlement 以控制每位用戶的權益狀態。
"""

from __future__ import annotations

from scripts.cron_wallet_monitor import _entitlement_filter


def _resolver_map(mapping):
    """產生 fake resolver：user_id -> can_use_scheduled_monitoring 的映射。"""

    def _r(uid):
        class _E:
            __slots__ = ("can_use_scheduled_monitoring",)

            def __init__(self, v):
                self.can_use_scheduled_monitoring = v

        return _E(mapping.get(uid, False))

    return _r


class TestEntitlementFilter:
    def test_gate_disabled_returns_all_unchanged(self):
        """gate 關閉時原樣回傳（安全漸進上線、既有行為不變）。"""
        users = [("u1", {"x": 1}), ("u2", {"x": 2}), ("u3", {"x": 3})]
        kept, skipped = _entitlement_filter(
            users, gate_enabled=False, resolver=_resolver_map({"u1": False})
        )
        assert kept == users
        assert skipped == 0

    def test_gate_enabled_keeps_only_active_premium(self):
        users = [("free", {}), ("active", {}), ("expired", {}), ("active2", {})]
        resolver = _resolver_map(
            {"active": True, "active2": True, "free": False, "expired": False}
        )
        kept, skipped = _entitlement_filter(users, gate_enabled=True, resolver=resolver)
        kept_ids = [uid for uid, _ in kept]
        assert kept_ids == ["active", "active2"]
        assert skipped == 2

    def test_gate_enabled_all_free(self):
        users = [("u1", {}), ("u2", {})]
        kept, skipped = _entitlement_filter(
            users, gate_enabled=True, resolver=_resolver_map({})
        )
        assert kept == []
        assert skipped == 2

    def test_resolver_exception_skips_user_fail_safe(self):
        """resolver 丟例外時該用戶被跳過（不誤放行付費能力）。"""

        def flaky_resolver(uid):
            if uid == "boom":
                raise RuntimeError("DB down")
            class _E:
                can_use_scheduled_monitoring = True
            return _E()

        users = [("ok", {}), ("boom", {})]
        kept, skipped = _entitlement_filter(
            users, gate_enabled=True, resolver=flaky_resolver
        )
        assert [uid for uid, _ in kept] == ["ok"]
        assert skipped == 1

    def test_empty_user_list(self):
        kept, skipped = _entitlement_filter(
            [], gate_enabled=True, resolver=_resolver_map({})
        )
        assert kept == []
        assert skipped == 0

    def test_settings_object_preserved_for_kept_users(self):
        """過濾後保留的用戶仍帶原本的 settings dict（下游 _process_user 依賴）。"""
        settings = {"monitored_wallets": ["addr1"], "min_amount_ton": 5}
        users = [("active", settings)]
        kept, skipped = _entitlement_filter(
            users, gate_enabled=True, resolver=_resolver_map({"active": True})
        )
        assert kept == [("active", settings)]
        assert skipped == 0


class TestFetchUsersWithMonitors:
    """候選人篩選推進 SQL（2026-09-25）：原本撈所有存過警示設定的人，到 Python 才
    濾掉 monitored_wallets 是空的（錢包全移除後設定列還在）。"""

    @staticmethod
    def _run(rows):
        from unittest.mock import MagicMock, patch

        from scripts import cron_wallet_monitor as mod

        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        with patch.object(mod, "get_connection", return_value=conn):
            users = mod._fetch_users_with_monitors()
        conn.close.assert_called_once()
        return users, cur.execute.call_args.args[0]

    def test_empty_monitor_lists_are_filtered_in_sql(self):
        _, sql = self._run([])
        assert "wallet_alert_settings->'monitored_wallets'" in sql
        assert "'[]'::jsonb" in sql and "'null'::jsonb" in sql

    def test_python_guard_still_drops_falsy_and_bad_rows(self):
        """SQL 只濾掉 Python 本來就會丟的列；Python 這層保留，結果與原本相同。"""
        import json

        rows = [
            ("u1", {"monitored_wallets": ["EQx"]}),
            ("u2", json.dumps({"monitored_wallets": [{"address": "0xabc"}]})),
            ("u3", {"monitored_wallets": ""}),
            ("u4", "not json"),
        ]
        users, _ = self._run(rows)
        assert [u for u, _ in users] == ["u1", "u2"]
