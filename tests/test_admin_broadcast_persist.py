"""Admin 廣播持久化的回歸（2026-09-07「broadcast history 都沒有顯示東西」）。

線上實證：admin_broadcasts 與 notifications 兩張表 0 行——廣播 API 回假
成功（sent_count 在記憶體算），兩個寫入 session 只有 flush/add、沒有
commit，session 關閉時全部回滾。功能從上線起就是靜默壞的（幽靈功能，
同 2026-09-04 清掉的 phantom flags 一類）。

本檔守「接線」：兩個寫入路徑的 commit 不得被移除（移除＝回到假成功＋
靜默回滾，正是線上壞掉的原狀）。行為面的驗證由部署後線上實測覆蓋
（發一則廣播 → history 出現、notifications 表有列）。

註：曾嘗試加 DB 行為測試（filter_by 差值斷言），但安全掃描器把測試裡
的動態 ORM 過濾一律誤判為注入而攔截——保留接線測試 + 線上實測的組合。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
NOTIFICATIONS_PY = REPO / "api" / "routers" / "admin" / "notifications.py"


class TestBroadcastCommits:
    def test_both_write_sessions_commit(self):
        """兩個寫入 session（notifications、broadcast+audit）都要 commit。"""
        py = NOTIFICATIONS_PY.read_text(encoding="utf-8")
        seg = py[py.index("async def broadcast_notification") : py.index("@router.get")]
        assert seg.count("await session.commit()") == 2, (
            "廣播的兩個寫入 session 都必須 commit——少了就是假成功＋靜默回滾"
        )
        # commit 必須在 flush/add 之後（寫入路徑的收尾）
        assert seg.index("await session.flush()") < seg.index("await session.commit()")
        assert seg.index("session.add(broadcast)") < seg.rindex("await session.commit()")

    def test_history_endpoint_reads_admin_broadcasts(self):
        """history 端點讀的是 AdminBroadcast 表——與寫入同一張表（合約）。"""
        py = NOTIFICATIONS_PY.read_text(encoding="utf-8")
        seg = py[py.index("@router.get") :]
        assert "AdminBroadcast.created_at.desc()" in seg
