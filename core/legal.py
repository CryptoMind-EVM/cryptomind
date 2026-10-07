"""服務條款／隱私政策的版本與使用者同意紀錄（c054 ``user_legal_acceptances``）。

2026-09-27 DANNY：條款改版後要使用者重新確認同意（條款第 15 條、隱私政策第 10 條）。
登入的人在網站上會看到「條款已更新」提示卡，按「我已閱讀並同意」才會消失（不擋功能），
同意紀錄一版一列、保留歷史。

**條款或隱私政策有重大變更時**：改 web/legal/*.html（含「最後更新」日期）→ 把
LEGAL_VERSION 改成同一天 → 部署，所有人就會再看到提示。tests/test_legal_consent.py
會檢查兩個頁面的「最後更新」日期跟 LEGAL_VERSION 一致。

同步 psycopg；API 端用 run_sync 包。寫入走 ``DatabaseBase.execute``（會 commit）。
"""

from __future__ import annotations

from core.database.base import DatabaseBase

LEGAL_VERSION = "2026-10-04"


def has_accepted(user_id: str, version: str = LEGAL_VERSION) -> bool:
    row = DatabaseBase.query_one(
        "SELECT 1 AS ok FROM user_legal_acceptances WHERE user_id = %s AND version = %s",
        (user_id, version),
    )
    return bool(row)


def record_acceptance(user_id: str, version: str = LEGAL_VERSION) -> None:
    """同一版重複按只留第一次的時間。"""
    DatabaseBase.execute(
        "INSERT INTO user_legal_acceptances (user_id, version) VALUES (%s, %s) "
        "ON CONFLICT (user_id, version) DO NOTHING",
        (user_id, version),
    )
