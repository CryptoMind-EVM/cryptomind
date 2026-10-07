"""agent_configs_repo.set_config 的 unit-of-work upsert 測試（Mixer Step 2）.

釘住三條路徑：既有列更新、並發首寫撞 PK 的單次重試（rollback → 重讀 →
再寫，後寫者勝）、重讀後列仍不存在時讓 IntegrityError 上拋。
用假 session 驅動（不依賴真 DB），專注 repo 自身的狀態機。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, Text, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.orm.agent_configs_repo import AgentConfigsRepository
from core.orm.models import UserAgentConfig

pytestmark = pytest.mark.unit


def _row(agent_id="general_research", model_selection=None, tools=None, skills=None):
    return SimpleNamespace(
        user_id="u1",
        agent_id=agent_id,
        model_selection=model_selection,
        tools=tools if tools is not None else [],
        skills=skills if skills is not None else [],
    )


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """足夠驅動 set_config 的最小 AsyncSession 分身。

    rows: get_config 的連續回傳值（每次 execute 消費一個）；
    commit_errors: 與每次 commit 對應的例外（None = 成功）。
    """

    def __init__(self, rows, commit_errors):
        self._rows = list(rows)
        self._commit_errors = list(commit_errors)
        self.added = []
        self.rollback_calls = 0
        self.commit_calls = 0

    async def execute(self, _stmt):
        return _FakeResult(self._rows.pop(0) if self._rows else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1
        error = self._commit_errors.pop(0) if self._commit_errors else None
        if error is not None:
            raise error

    async def rollback(self):
        self.rollback_calls += 1

    async def refresh(self, _obj):
        return None


def _integrity_error():
    return IntegrityError("INSERT ...", {}, Exception("dup key"))


class TestSetConfigUoW:
    @pytest.mark.parametrize("retry", [False, True])
    @pytest.mark.parametrize("field", ["tools", "model_selection"])
    async def test_partial_update_preserves_omitted_field_even_on_insert_race(self, retry, field):
        model = {"provider": "openai", "model": "existing-model"}
        winner = _row(model_selection=model, tools=["web_search"])
        db = _FakeSession(
            rows=[None, winner] if retry else [winner],
            commit_errors=[_integrity_error(), None] if retry else [None],
        )
        changes = {"tools": ["fetch_url"]} if field == "tools" else {"model_selection": None}
        result = await AgentConfigsRepository().set_config(db, "p1", "general_research", "u1", **changes)
        assert result.tools == (["fetch_url"] if field == "tools" else ["web_search"])
        assert result.model_selection == (model if field == "tools" else None)
        assert db.rollback_calls == int(retry)

    async def test_existing_row_updated_in_place(self):
        existing = _row(model_selection={"provider": "openai", "model": "m"})
        db = _FakeSession(rows=[existing], commit_errors=[None])
        result = await AgentConfigsRepository().set_config(db, "p1", "general_research", "u1", None, ["web_search"])
        assert result is existing
        assert existing.model_selection is None
        assert existing.tools == ["web_search"]
        assert db.added == []
        assert db.rollback_calls == 0

    async def test_concurrent_first_write_retries_once_and_wins(self):
        # 第一次 get_config：無列 → 走 INSERT；commit 撞 PK。
        # 第二次 get_config（retry 內）：對方已建列 → UPDATE 路徑再 commit 成功。
        winner_row = _row(model_selection={"provider": "x", "model": "y"})
        db = _FakeSession(rows=[None, winner_row], commit_errors=[_integrity_error(), None])
        result = await AgentConfigsRepository().set_config(db, "p1", "general_research", "u1", None, ["web_search"])
        assert result is winner_row
        assert winner_row.model_selection is None
        assert winner_row.tools == ["web_search"]
        assert len(db.added) == 1  # 首寫的 INSERT 嘗試
        assert db.rollback_calls == 1
        assert db.commit_calls == 2

    async def test_row_vanished_after_rollback_reraises(self):
        # rollback 後重讀仍無列（極罕見交錯）→ 讓 IntegrityError 上拋。
        db = _FakeSession(rows=[None, None], commit_errors=[_integrity_error()])
        with pytest.raises(IntegrityError):
            await AgentConfigsRepository().set_config(db, "p1", "general_research", "u1", None, [])
        assert db.rollback_calls == 1


class TestSkillsColumn:
    """Mixer Step 4：skills 欄獨立 upsert（語意與 tools 完全一致）。"""

    @pytest.mark.parametrize("retry", [False, True])
    async def test_skills_partial_update_preserves_tools_and_model(self, retry):
        model = {"provider": "openai", "model": "m"}
        winner = _row(model_selection=model, tools=["web_search"], skills=["old-skill"])
        db = _FakeSession(
            rows=[None, winner] if retry else [winner],
            commit_errors=[_integrity_error(), None] if retry else [None],
        )
        result = await AgentConfigsRepository().set_config(
            db, "p1", "general_research", "u1",
            skills=["crypto-technical-analysis"],
        )
        assert result.skills == ["crypto-technical-analysis"]
        assert result.tools == ["web_search"]
        assert result.model_selection == model
        assert db.rollback_calls == int(retry)

    async def test_skills_reset_to_empty_keeps_tools(self):
        """skills=[]（未設定）只清自己，tools 保留——列是三欄共用的。"""
        existing = _row(tools=["web_search"], skills=["a", "b"])
        db = _FakeSession(rows=[existing], commit_errors=[None])
        result = await AgentConfigsRepository().set_config(
            db, "p1", "general_research", "u1", skills=[]
        )
        assert result is existing
        assert result.skills == []
        assert result.tools == ["web_search"]

    async def test_skills_absent_never_touched_by_tools_update(self):
        """只更新 tools 時 skills 不進 kwargs（absent＝不更動）。"""
        existing = _row(tools=[], skills=["keep-me"])
        db = _FakeSession(rows=[existing], commit_errors=[None])
        result = await AgentConfigsRepository().set_config(
            db, "p1", "general_research", "u1", tools=["web_search"]
        )
        assert result.skills == ["keep-me"]


@pytest.fixture
def orm_engine():
    """Real ORM flush/constraint checks in memory; no application DB or global type hooks."""
    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("users", metadata, Column("user_id", Text, primary_key=True))
    # c044：user_agent_configs 的主鍵改成 (preset_id, agent_id) 並外鍵指向
    # user_agent_presets，所以這份 in-memory metadata 也要有被參照的表。
    Table("user_agent_presets", metadata, Column("preset_id", Text, primary_key=True))
    table = UserAgentConfig.__table__.to_metadata(metadata)
    for column in table.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    metadata.create_all(engine)
    yield engine
    engine.dispose()


class _AsyncSessionAdapter:
    """Awaitable facade over an isolated sync session; inject one competing commit."""

    def __init__(self, session, before_commit=None):
        self.session = session
        self.before_commit = before_commit
        self.rollbacks = 0

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, instance):
        self.session.add(instance)

    async def commit(self):
        if self.before_commit:
            callback, self.before_commit = self.before_commit, None
            callback()
        self.session.commit()

    async def rollback(self):
        self.rollbacks += 1
        self.session.rollback()

    async def refresh(self, instance):
        self.session.refresh(instance)


@pytest.mark.parametrize("first_insert_race", [False, True])
@pytest.mark.parametrize("field", ["tools", "model_selection"])
async def test_real_orm_partial_writes_preserve_other_column(orm_engine, first_insert_race, field):
    model = {"provider": "openai", "model": "fixture-model"}

    def competing_write():
        with Session(orm_engine) as other:
            other.add(UserAgentConfig(
                preset_id="p1",
                user_id="u1", agent_id="general_research", model_selection=model,
                tools=["web_search"], skills=["reserved-skill"],
            ))
            other.commit()

    statements = []
    event.listen(orm_engine, "before_cursor_execute", lambda conn, cursor, stmt, params, ctx, many: statements.append(stmt))
    if not first_insert_race:
        competing_write()
    with Session(orm_engine) as session:
        db = _AsyncSessionAdapter(session, competing_write if first_insert_race else None)
        changes = {"tools": ["fetch_url"]} if field == "tools" else {"model_selection": None}
        result = await AgentConfigsRepository().set_config(db, "p1", "general_research", "u1", **changes)
        assert result.tools == (["fetch_url"] if field == "tools" else ["web_search"])
        assert result.model_selection == (model if field == "tools" else None)
        assert result.skills == ["reserved-skill"]
        assert db.rollbacks == int(first_insert_race)
    updates = [stmt.split(" WHERE ")[0] for stmt in statements if stmt.startswith("UPDATE")]
    assert len(updates) == 1
    omitted = "model_selection" if field == "tools" else "tools"
    assert omitted not in updates[0]


@pytest.mark.parametrize("changes", [{"tools": ["web_search"]}, {"model_selection": None}])
async def test_real_orm_first_insert_defaults(orm_engine, changes):
    with Session(orm_engine) as session:
        row = await AgentConfigsRepository().set_config(
            _AsyncSessionAdapter(session), "p1", "general_research", "u1", **changes,
        )
        assert row.tools == changes.get("tools", [])
        assert row.model_selection is None
        assert row.skills == []
