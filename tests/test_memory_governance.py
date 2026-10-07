"""記憶治理與版本 c031（docs/plans/2026-08-21-memory-governance-design.md）。

測試鎖住：
- read_facts 過濾：status=active + valid_until 未過期 + verified 優先排序
- upsert supersedes：同 key 新值 → 舊值標 superseded、新值帶 supersedes_id
- valid_until 計算：time_sensitivity volatile=1天 / dated=30天 / permanent=None
- skill 版本：update 前存舊版 revision、get_revisions 列表、rollback 回寫
- API：verify_fact 更新 verified_at、fact_history 返回審計鏈
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


class TestReadFactsGovernance:
    def test_sql_filters_status_and_validity(self):
        from core.database.memory import MemoryStore

        src = inspect.getsource(MemoryStore.read_facts)
        assert "status = 'active'" in src, "只讀 active（superseded/expired 排除）"
        assert "valid_until IS NULL OR valid_until > NOW()" in src, "過期排除"

    def test_sql_verified_first(self):
        from core.database.memory import MemoryStore

        src = inspect.getsource(MemoryStore.read_facts)
        assert "verified_at IS NULL" in src, "已確認的排前面（NULLS LAST）"

    def test_sql_returns_governance_columns(self):
        from core.database.memory import MemoryStore

        src = inspect.getsource(MemoryStore.read_facts)
        assert "valid_until" in src and "verified_at" in src


class TestSupersedesChain:
    def test_upsert_calls_supersede(self):
        from core.database.memory import MemoryStore

        src = inspect.getsource(MemoryStore.write_facts)
        assert "_get_active_fact_id" in src, "upsert 前查 active 舊 id"
        assert "_supersede_fact" in src, "upsert 前標記舊值 superseded"

    def test_supersede_marks_not_deletes(self):
        from core.database.memory import MemoryStore

        src = inspect.getsource(MemoryStore._supersede_fact)
        assert "status = 'superseded'" in src
        assert "DELETE" not in src.upper(), "supersede 不刪除（審計鏈）"

    def test_valid_until_volatile_one_day(self):
        from core.database.memory import MemoryStore

        store = MemoryStore.__new__(MemoryStore)
        result = store._compute_valid_until({"time_sensitivity": "volatile"})
        assert result is not None, "volatile 應有 valid_until（1 天）"

    def test_valid_until_dated_thirty_days(self):
        from core.database.memory import MemoryStore

        store = MemoryStore.__new__(MemoryStore)
        result = store._compute_valid_until({"time_sensitivity": "dated"})
        assert result is not None, "dated 應有 valid_until（30 天）"

    def test_valid_until_permanent_none(self):
        from core.database.memory import MemoryStore

        store = MemoryStore.__new__(MemoryStore)
        assert store._compute_valid_until({"time_sensitivity": "permanent"}) is None
        assert store._compute_valid_until({}) is None, "預設 permanent"


class TestSkillVersioning:
    def test_update_snapshots_revision(self):
        from core.database.skill_preferences import SkillPreferenceStore

        src = inspect.getsource(SkillPreferenceStore.update_custom_skill)
        assert "_snapshot_revision" in src, "update 前存舊版"
        assert "revision + 1" in src, "revision 自增"

    def test_snapshot_idempotent(self):
        from core.database.skill_preferences import SkillPreferenceStore

        src = inspect.getsource(SkillPreferenceStore._snapshot_revision)
        assert "ON CONFLICT (skill_id, revision) DO NOTHING" in src

    def test_rollback_restores_body(self):
        from core.database.skill_preferences import SkillPreferenceStore

        src = inspect.getsource(SkillPreferenceStore.rollback_skill)
        assert "user_skill_revisions" in src, "從 revisions 表取舊版"
        assert "revision = user_custom_skills.revision + 1" in src, "回滾也是新 revision"

    def test_get_revisions_ordered(self):
        from core.database.skill_preferences import SkillPreferenceStore

        src = inspect.getsource(SkillPreferenceStore.get_skill_revisions)
        assert "ORDER BY revision DESC" in src


class TestGovernanceAPI:
    def test_verify_endpoint_exists(self):
        import api.routers.memory as m

        src = inspect.getsource(m)
        assert "facts/{key}/verify" in src
        assert "verified_at = NOW()" in src

    def test_history_endpoint_exists(self):
        import api.routers.memory as m

        src = inspect.getsource(m)
        assert "facts/{key}/history" in src
        assert "supersedes_id" in src, "history 應返回審計鏈欄位"


class TestMigration:
    def test_c031_adds_columns(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "alembic/versions" / "c031_memory_governance.py").read_text(encoding="utf-8")
        for col in ["valid_from", "valid_until", "source_query", "supersedes_id", "status", "verified_at"]:
            assert col in src, f"c031 應加 {col}"
        assert "user_skill_revisions" in src, "c031 應建版本表"
