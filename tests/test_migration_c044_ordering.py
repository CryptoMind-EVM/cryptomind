"""c044 遷移的順序守衛（2026-09-04 實測踩到）。

第一版把「複製到其餘 preset」的 INSERT 寫在 DROP 舊主鍵**之前**：此時
PRIMARY KEY (user_id, agent_id) 還在，第二列必然撞鍵。再配上
ON CONFLICT DO NOTHING，資料就靜默消失——alembic 回報 upgrade 成功，
實測兩個 preset 只回填了一個。

這支不連資料庫，只釘住 SQL 的相對順序與「不得吞掉衝突」。真正的資料
搬移已在 dev DB 上雙向實跑驗證過（見 PR 說明）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/c044_preset_scoped_agent_configs.py"
)


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    src = MIGRATION.read_text(encoding="utf-8")
    start = src.index("def upgrade()")
    return src[start : src.index("def downgrade()")]


def test_old_primary_key_is_dropped_before_the_copy(upgrade_sql: str):
    drop = upgrade_sql.index("DROP CONSTRAINT IF EXISTS user_agent_configs_pkey")
    insert = upgrade_sql.index("INSERT INTO user_agent_configs")
    assert drop < insert, (
        "複製列的 INSERT 跑在舊主鍵仍存在時，第二個 preset 的列會撞 "
        "PRIMARY KEY (user_id, agent_id)——資料遺失且遷移回報成功"
    )


def test_copy_does_not_swallow_conflicts(upgrade_sql: str):
    insert = upgrade_sql.index("INSERT INTO user_agent_configs")
    tail = upgrade_sql[insert : insert + 900]
    assert "ON CONFLICT" not in tail, (
        "回填的 INSERT 不得加 ON CONFLICT——此刻已無唯一約束，"
        "真的撞上代表前提錯了，必須大聲失敗而不是靜默丟資料"
    )


def test_new_key_and_cascade_are_both_present(upgrade_sql: str):
    assert "PRIMARY KEY (preset_id, agent_id)" in upgrade_sql
    assert "REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE" in upgrade_sql


def test_orphan_rows_are_removed(upgrade_sql: str):
    """沒有任何 preset 的使用者，其列在執行期本來就讀不到，不可留成孤兒。"""
    assert "DELETE FROM user_agent_configs WHERE preset_id IS NULL" in upgrade_sql


def test_downgrade_exists_and_restores_the_old_key():
    src = MIGRATION.read_text(encoding="utf-8")
    down = src[src.index("def downgrade()") :]
    assert "PRIMARY KEY (user_id, agent_id)" in down
    assert "DROP COLUMN IF EXISTS preset_id" in down
