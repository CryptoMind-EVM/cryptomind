"""EVM tx hash 大小寫不同＝同一筆交易：Premium 不能用換大小寫的 hash 重複開通。

2026-09-25（#880 盤查時發現）：verify_evm_usdc_tx 原樣回傳使用者送的 hash，
upgrade_to_pro 用字串完全相等去重——鏈上節點不分 hash 大小寫，同一筆付款＋
同一張訂單（7 天內有效）換個大小寫就能再領一次 Premium。
"""

import pytest

pytestmark = pytest.mark.unit


class _FakeDB:
    """最小替身：membership_payments 的 tx_hash 欄位，照 SQL 字面決定比對方式。"""

    def __init__(self, stored):
        self.stored = list(stored)
        self.inserted = []
        db = self

        class _Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                self._row = None
                if "FROM membership_payments" in sql and "SELECT" in sql:
                    needle = params[0]
                    if "lower(tx_hash)" in sql:
                        hit = any(s.lower() == needle.lower() for s in db.stored)
                    else:
                        hit = needle in db.stored
                    self._row = ("someone",) if hit else None
                elif "FROM users WHERE user_id" in sql:
                    self._row = ("free", None, False)
                elif "INSERT INTO membership_payments" in sql:
                    db.inserted.append(params[3])

            def fetchone(self):
                return self._row

        self._cur = _Cur()

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


HEX = "0x" + "ab12" * 16


def _run(monkeypatch, stored, tx):
    import core.database.user as user_db

    db = _FakeDB(stored)
    monkeypatch.setattr(user_db, "get_connection", lambda: db)
    return user_db, db, lambda: user_db.upgrade_to_pro("u1", 1, tx, amount=5.001)


def test_uppercase_variant_of_a_used_hash_is_rejected(monkeypatch):
    _, db, call = _run(monkeypatch, [HEX], HEX.upper().replace("0X", "0x"))
    with pytest.raises(ValueError):
        call()
    assert db.inserted == []


def test_mixed_case_legacy_row_still_blocks_lowercase_claim(monkeypatch):
    """舊資料可能存成大小寫混合；新送的小寫版本也要擋。"""
    legacy = "0x" + "AB12" * 16
    _, db, call = _run(monkeypatch, [legacy], HEX)
    with pytest.raises(ValueError):
        call()


def test_new_hex_hash_is_stored_lowercase(monkeypatch):
    _, db, call = _run(monkeypatch, [], "0x" + "AB12" * 16)
    assert call() is True
    assert db.inserted == [HEX]


def test_non_hex_hash_keeps_exact_case(monkeypatch):
    """TON 的 hash 是 base64（大小寫有意義）；admin_grant_… 等內部標記也原樣存。"""
    ton = "Ab+cD/eF" * 5 + "Zz=="
    _, db, call = _run(monkeypatch, [ton.lower()], ton)
    assert call() is True
    assert db.inserted == [ton]
