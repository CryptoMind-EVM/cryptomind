"""記憶分層隔離矩陣 — MemoryStore agent 維度（Model Mixer Step 4）.

設計：私有層＝獨立表 ``user_facts_private``（共享表 user_facts 刻意不動）。
本檔把隔離邊界釘死在 SQL 層：agent 視角的讀寫必須走私有表且帶 agent_id、
共享視角絕不能碰到私有表、instance cache 按 agent 分 instance。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_memory_cache_state():
    from core.database import memory as mem_mod

    mem_mod._reset_for_testing()
    yield
    mem_mod._reset_for_testing()


class _SqlRecorder:
    """捕捉 DatabaseBase 呼叫（method, sql, params）。"""

    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def query_all(self, sql, params=None):
        self.calls.append(("query_all", sql, params))
        return self._rows

    def execute(self, sql, params=None):
        self.calls.append(("execute", sql, params))
        return None


def _recorder(rows=None):
    rec = _SqlRecorder(rows)
    return rec, patch("core.database.memory.DatabaseBase", rec)


def _sql(rec, method="query_all", needle=""):
    return [
        c[1]
        for c in rec.calls
        if c[0] == method and needle in c[1]
    ]


class TestReadIsolation:
    def test_agent_view_reads_shared_and_own_private(self):
        """agent 視角：查共享表（原查詢）＋私有表（帶 agent_id 條件）。"""
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1", agent_id="finance_markets").read_facts()
        private_sql = _sql(rec, needle="user_facts_private")
        assert private_sql, "agent 視角必須查私有表"
        assert "agent_id = %s" in private_sql[0], private_sql[0]
        assert any(
            "finance_markets" in str(c[2]) for c in rec.calls
            if "user_facts_private" in c[1]
        ), "私有表查詢必須以 store 的 agent_id 綁定"

    def test_shared_view_never_touches_private_table(self):
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1").read_facts()
        assert not _sql(rec, needle="user_facts_private"), "共享視角不得讀私有表"

    def test_private_rows_marked_with_agent_id(self):
        shared_row = {
            "key": "nick", "value": "叫老王", "confidence": "high",
            "source_turn": 1, "category": "fact", "valid_until": None,
            "status": "active", "verified_at": None,
        }
        private_row = dict(
            shared_row, key="style", value="偏好保守", category="preference"
        )
        rec = _SqlRecorder(rows=[shared_row])

        def query_all(sql, params=None):
            rec.calls.append(("query_all", sql, params))
            return [private_row] if "user_facts_private" in sql else [shared_row]

        rec.query_all = query_all
        with patch("core.database.memory.DatabaseBase", rec):
            facts = MemoryStore(user_id="u1", agent_id="finance_markets").read_facts()
        assert facts["nick"]["agent_id"] is None
        assert facts["style"]["agent_id"] == "finance_markets"


class TestWriteOwnership:
    def test_agent_view_writes_private_table(self):
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1", agent_id="onchain_security").write_facts(
                [{"key": "k1", "value": "v1", "category": "fact"}]
            )
        insert_sql = _sql(rec, method="execute", needle="INSERT INTO user_facts_private")
        assert insert_sql, "agent 視角寫入必須走私有表"
        assert "ON CONFLICT (user_id, agent_id, key)" in insert_sql[0]
        params = next(
            c[2] for c in rec.calls if "user_facts_private" in str(c[1])
        )
        assert "onchain_security" in str(params)

    def test_agent_view_never_writes_shared_table(self):
        """§7：共享層只允許系統寫入——agent 視角寫入不得出現 user_facts INSERT。"""
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1", agent_id="finance_markets").write_facts(
                [{"key": "k1", "value": "v1", "category": "fact"}]
            )
        shared_inserts = [
            s for s in _sql(rec, method="execute", needle="INSERT INTO")
            if "user_facts_private" not in s
        ]
        assert not shared_inserts, shared_inserts

    def test_shared_view_writes_shared_table(self):
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1").write_facts(
                [{"key": "k1", "value": "v1", "category": "fact"}]
            )
        assert _sql(rec, method="execute", needle="INSERT INTO user_facts")


class TestDeletePermission:
    def test_agent_delete_targets_private_then_shared(self):
        """agent 視角刪除：先刪自身私有；共享刪除由使用者 consent 授權（fallback）。"""
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1", agent_id="finance_markets").delete_fact("k1")
        del_private = _sql(rec, method="execute", needle="DELETE FROM user_facts_private")
        assert del_private and "agent_id = %s" in del_private[0]

    def test_shared_delete_only_shared(self):
        from core.database.memory import MemoryStore

        rec, p = _recorder()
        with p:
            MemoryStore(user_id="u1").delete_fact("k1")
        assert not _sql(rec, method="execute", needle="user_facts_private")


class TestInstanceCache:
    def test_factory_separates_same_user_by_agent(self):
        from core.database.memory import get_memory_store

        a = get_memory_store("u1", session_id="s", agent_id="finance_markets")
        b = get_memory_store("u1", session_id="s", agent_id="onchain_security")
        shared = get_memory_store("u1", session_id="s")
        assert a is not b and a is not shared
        assert a.agent_id == "finance_markets"

    def test_same_agent_returns_same_instance(self):
        from core.database.memory import get_memory_store

        a1 = get_memory_store("u1", session_id="s", agent_id="finance_markets")
        a2 = get_memory_store("u1", session_id="s", agent_id="finance_markets")
        assert a1 is a2

    def test_instance_cache_is_bounded(self, monkeypatch):
        """快取要有上限：以前是永不清的 dict，每個對過話的 session 都留一份。"""
        from cachetools import TTLCache

        from core.database import memory

        monkeypatch.setattr(memory, "_memory_stores", TTLCache(maxsize=3, ttl=3600))
        for i in range(10):
            memory.get_memory_store("u1", session_id=f"s{i}")
        assert len(memory._memory_stores) == 3
        # 被逐出後再要一次：照樣拿得到（MemoryStore 不持狀態，重建即可）
        again = memory.get_memory_store("u1", session_id="s0")
        assert again.session_id == "s0"

    def test_default_cache_has_a_ceiling(self):
        from core.database import memory

        assert memory._memory_stores.maxsize <= 4096


class TestFactsToText:
    def test_sections_split_shared_and_private(self):
        def query_all(sql, params=None):
            if "user_facts_private" in sql:
                return [{
                    "key": "style", "value": "偏好保守操作", "confidence": "high",
                    "source_turn": 2, "category": "preference", "valid_until": None,
                    "status": "active", "verified_at": None,
                }]
            return [{
                "key": "nick", "value": "叫老王", "confidence": "high",
                "source_turn": 1, "category": "fact", "valid_until": None,
                "status": "active", "verified_at": None,
            }]

        rec = _SqlRecorder()
        rec.query_all = query_all
        with patch("core.database.memory.DatabaseBase", rec):
            text = MemoryStore(user_id="u1", agent_id="finance_markets").facts_to_text()
        assert "叫老王" in text and "偏好保守操作" in text
        assert "agent-private" in text
        assert text.index("叫老王") < text.index("偏好保守操作")


from core.database.memory import MemoryStore  # noqa: E402 — after fixtures for clarity


class TestConsentWriteIdentity:
    def test_single_profile_preset_writes_private(self):
        from core.agents.manager.claw_loop import _memory_agent_id_for_state

        assert (
            _memory_agent_id_for_state(
                {"preset_config": {"agent_ids": ["finance_markets"]}}
            )
            == "finance_markets"
        )

    def test_undetermined_identity_falls_back_to_shared(self):
        from core.agents.manager.claw_loop import _memory_agent_id_for_state

        assert _memory_agent_id_for_state(
            {"preset_config": {"agent_ids": ["a", "b"]}}
        ) is None
        assert _memory_agent_id_for_state({}) is None
        assert _memory_agent_id_for_state({"preset_config": {}}) is None


class TestSchemaSync:
    def test_orm_user_fact_untouched(self):
        from core.orm.models import UserFact

        assert not hasattr(UserFact, "agent_id"), "共享表 user_facts 不得加欄"

    def test_orm_private_model(self):
        from core.orm.models import UserFactPrivate

        assert UserFactPrivate.__tablename__ == "user_facts_private"
        assert hasattr(UserFactPrivate, "agent_id")

    def test_orm_user_memory_has_agent_id_and_scope(self):
        from core.orm.models import UserMemory

        assert hasattr(UserMemory, "agent_id") and hasattr(UserMemory, "scope")

    def test_schema_py_registers_private_table(self):
        import re
        from pathlib import Path

        src = Path("core/database/schema.py").read_text(encoding="utf-8")
        assert "create_user_facts_private_table" in src
        assert re.search(
            r'\("user_facts_private", create_user_facts_private_table\)', src
        )


class TestAgentContextvar:
    def test_single_profile_id_from_context(self):
        from core.agents.base_react_agent import _single_profile_id_from_context

        assert (
            _single_profile_id_from_context(
                {"preset_config": {"agent_ids": ["finance_markets"]}}
            )
            == "finance_markets"
        )
        assert (
            _single_profile_id_from_context(
                {"preset_config": {"agent_ids": ["a", "b"]}}
            )
            is None
        )
        assert _single_profile_id_from_context({}) is None
        assert _single_profile_id_from_context(None) is None
        assert _single_profile_id_from_context({"preset_config": None}) is None

    def test_contextvar_roundtrip_and_reset(self):
        from core.tools.key_resolver import (
            get_current_agent_id,
            reset_current_agent_id,
            set_current_agent_id,
        )

        token = set_current_agent_id("finance_markets")
        try:
            assert get_current_agent_id() == "finance_markets"
        finally:
            reset_current_agent_id(token)
        assert get_current_agent_id() is None


class TestOnConflictArbiterContract:
    """P0 守門：write_long_term／write_compact_state 的 ON CONFLICT 必須有
    對應的 unique constraint 當 arbiter（c043 刻意不動舊 constraint 的原因）。
    誰改了 c043 或這兩個 SQL，這裡先紅。"""

    def _insert_sql(self, store_method, *args):
        rec = _SqlRecorder()
        with patch("core.database.memory.DatabaseBase", rec):
            with patch("core.database.memory._get_redis_sync", return_value=None):
                try:
                    store_method(*args)
                except Exception:
                    pass
        return [c[1] for c in rec.calls if "INSERT INTO user_memory" in c[1]]

    def test_write_long_term_arbiter_matches_kept_constraint(self):
        from core.database.memory import MemoryStore

        sqls = self._insert_sql(MemoryStore(user_id="u1").write_long_term, "ctx")
        assert sqls, "write_long_term 未執行 INSERT"
        assert "ON CONFLICT (user_id, session_id, memory_type)" in sqls[0]

    def test_write_compact_state_arbiter_matches_kept_constraint(self):
        from core.database.memory import CompactedSessionState, MemoryStore

        state = CompactedSessionState(
            goal="g",
            progress="p",
            open_questions="",
            next_steps="",
            turn_index=1,
            updated_at="2026-09-03T00:00:00Z",
        )
        sqls = self._insert_sql(MemoryStore(user_id="u1", session_id="s1").write_compact_state, state)
        assert sqls, "write_compact_state 未執行 INSERT"
        assert "ON CONFLICT (user_id, session_id, memory_type)" in sqls[0]

    def test_c043_keeps_old_constraint(self):
        from pathlib import Path

        src = Path("alembic/versions/c043_memory_agent_scope.py").read_text(
            encoding="utf-8"
        )
        assert "drop_constraint" not in src, "c043 不得動 user_memory constraint"


class TestContextCacheAgentDimension:
    """P1 守門：agent 視角的 context 快取必須與共享視角分 key；任何寫入
    清掉該使用者全部視角變體。"""

    def test_scope_includes_agent(self):
        from core.database.memory import MemoryStore

        shared = MemoryStore("u1").scope
        agent_a = MemoryStore("u1", agent_id="finance_markets").scope
        agent_b = MemoryStore("u1", agent_id="onchain_security").scope
        assert shared == "u1"
        assert agent_a == "u1|agent:finance_markets"
        assert agent_b != agent_a

    def test_l1_cache_split_by_agent_view(self):
        from core.database.memory import MemoryStore, _mem_l1_get

        shared_store = MemoryStore("u1")
        agent_store = MemoryStore("u1", agent_id="finance_markets")

        with patch.object(
            type(shared_store), "_read_from_db", return_value="shared-ctx"
        ):
            shared_store.get_memory_context()
        with patch.object(
            type(agent_store), "_read_from_db", return_value="agent-ctx"
        ):
            agent_store.get_memory_context()

        assert _mem_l1_get("u1") == "shared-ctx"
        assert _mem_l1_get("u1|agent:finance_markets") == "agent-ctx"

    def test_shared_write_invalidates_all_agent_variants(self):
        from core.database.memory import MemoryStore, _mem_l1_get, _mem_l1_set

        _mem_l1_set("u1", "shared-ctx")
        _mem_l1_set("u1|agent:finance_markets", "agent-ctx")
        _mem_l1_set("u1|agent:onchain_security", "agent-ctx-2")

        shared_store = MemoryStore("u1")
        with patch("core.database.memory._get_redis_sync", return_value=None):
            with patch("core.database.memory.DatabaseBase.execute"):
                shared_store.write_long_term("new memory")

        assert _mem_l1_get("u1") is None
        assert _mem_l1_get("u1|agent:finance_markets") is None
        assert _mem_l1_get("u1|agent:onchain_security") is None

    def test_private_write_invalidates_all_variants_of_user(self):
        from core.database.memory import MemoryStore, _mem_l1_get, _mem_l1_set

        _mem_l1_set("u1", "shared-ctx")
        _mem_l1_set("u1|agent:finance_markets", "agent-ctx")

        agent_store = MemoryStore("u1", agent_id="finance_markets")
        with patch("core.database.memory._get_redis_sync", return_value=None):
            with patch("core.database.memory.DatabaseBase.execute"):
                agent_store.write_facts(
                    [{"key": "k", "value": "v", "category": "fact"}]
                )

        assert _mem_l1_get("u1") is None
        assert _mem_l1_get("u1|agent:finance_markets") is None

    def test_other_user_cache_unaffected(self):
        from core.database.memory import MemoryStore, _mem_l1_get, _mem_l1_set

        _mem_l1_set("u2", "other-user")
        with patch("core.database.memory._get_redis_sync", return_value=None):
            with patch("core.database.memory.DatabaseBase.execute"):
                MemoryStore("u1").write_long_term("new memory")
        assert _mem_l1_get("u2") == "other-user"
