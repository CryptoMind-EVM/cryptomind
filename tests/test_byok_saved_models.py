"""Unit tests for BYOK per-provider saved models (user_saved_models).

設計背景：綁定單位過去是 provider（user_api_keys 一 provider 一列），
同 provider 綁第二個模型會覆蓋第一個。user_saved_models 記住
「這個 provider 保存過哪些模型」：
- 同 provider + 同 model 重綁 → 更新金鑰，不重複
- 同 provider + 不同 model → 清單多一筆
- 金鑰仍一 provider 一把；active model 仍是 user_api_keys.model_selection

Covers:
- ORM model shape (unique constraint on user_id/provider/model)
- repo: save_user_api_key / save_user_model_selection also record saved model
- repo: get_all_user_api_keys returns per-provider ``models`` list
- repo: delete_saved_model branch behaviors (repoint active / remove provider)
- router: DELETE /api/user/api-keys/{provider}?model=... routing
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from core.orm.user_api_keys_repo import user_api_keys_repo
from utils.encryption import encrypt_api_key


def to_pg_sql(stmt) -> str:
    """Compile a SQLAlchemy statement against the PostgreSQL dialect."""
    return str(stmt.compile(dialect=postgresql.dialect()))


class FakeResult:
    def __init__(self, *, rows=None, scalar=..., rowcount=0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def scalar_one_or_none(self):
        return None if self._scalar is ... else self._scalar


class FakeSession:
    """Records executed statements; returns pre-programmed results in order."""

    def __init__(self, results=None):
        self.executed = []
        self._results = list(results or [])

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._results.pop(0) if self._results else FakeResult()


# ---------------------------------------------------------------------------
# ORM model shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_user_saved_model_orm_shape():
    from core.orm.models import UserSavedModel

    assert UserSavedModel.__tablename__ == "user_saved_models"
    cols = {c.name for c in UserSavedModel.__table__.columns}
    assert {"user_id", "provider", "model"}.issubset(cols)

    uniques = [
        c
        for c in UserSavedModel.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    ]
    assert any(
        {col.name for col in c.columns} == {"user_id", "provider", "model"}
        for c in uniques
    ), "user_saved_models must be unique on (user_id, provider, model)"


# ---------------------------------------------------------------------------
# add_saved_model
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_add_saved_model_conflict_ignore():
    s = FakeSession()
    result = await user_api_keys_repo.add_saved_model(
        "u1", "openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", session=s
    )
    assert result["success"] is True
    assert len(s.executed) == 1
    sql = to_pg_sql(s.executed[0])
    assert "INSERT INTO user_saved_models" in sql
    assert "ON CONFLICT (user_id, provider, model) DO NOTHING" in sql


@pytest.mark.unit
async def test_add_saved_model_rejects_blank_model():
    s = FakeSession()
    result = await user_api_keys_repo.add_saved_model(
        "u1", "openrouter", "  ", session=s
    )
    assert result["success"] is False
    assert s.executed == []


# ---------------------------------------------------------------------------
# save_user_api_key records the saved model
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_save_user_api_key_also_records_saved_model():
    s = FakeSession()
    result = await user_api_keys_repo.save_user_api_key(
        "u1", "openrouter", "sk-or-test", model="minimaxai/minimax-m3", session=s
    )
    assert result["success"] is True
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert any("INSERT INTO user_api_keys" in q for q in sqls)
    assert any("INSERT INTO user_saved_models" in q for q in sqls)


@pytest.mark.unit
async def test_save_user_api_key_without_model_skips_saved_models():
    s = FakeSession()
    result = await user_api_keys_repo.save_user_api_key(
        "u1", "tavily", "tvly-test", model=None, session=s
    )
    assert result["success"] is True
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert not any("user_saved_models" in q for q in sqls)


# ---------------------------------------------------------------------------
# save_user_model_selection keeps the invariant "active model is saved"
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_save_user_model_selection_records_saved_model():
    s = FakeSession(results=[FakeResult(rowcount=1), FakeResult()])
    result = await user_api_keys_repo.save_user_model_selection(
        "u1", "openrouter", "minimaxai/minimax-m3", session=s
    )
    assert result["success"] is True
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert any("UPDATE user_api_keys" in q for q in sqls)
    assert any("INSERT INTO user_saved_models" in q for q in sqls)


@pytest.mark.unit
async def test_save_user_model_selection_no_key_row_skips_saved_model():
    s = FakeSession(results=[FakeResult(rowcount=0)])
    result = await user_api_keys_repo.save_user_model_selection(
        "u1", "openrouter", "minimaxai/minimax-m3", session=s
    )
    assert result["success"] is False
    assert len(s.executed) == 1


# ---------------------------------------------------------------------------
# get_all_user_api_keys returns the per-provider models list
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_all_user_api_keys_includes_models_list():
    encrypted = encrypt_api_key("sk-or-test")
    key_rows = [("openrouter", encrypted, "model-a", None)]
    saved_rows = [("openrouter", "model-a"), ("openrouter", "model-b")]
    s = FakeSession(results=[FakeResult(rows=key_rows), FakeResult(rows=saved_rows)])

    keys = await user_api_keys_repo.get_all_user_api_keys("u1", kind="llm", session=s)

    assert keys["openrouter"]["model"] == "model-a"
    assert keys["openrouter"]["models"] == ["model-a", "model-b"]
    # providers without keys still get a stable default shape
    unbound = [p for p, info in keys.items() if not info["has_key"]]
    assert unbound, "expected default entries for unbound providers"
    assert all(keys[p]["models"] == [] for p in unbound)


# ---------------------------------------------------------------------------
# delete_saved_model
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_delete_saved_model_not_found():
    s = FakeSession(results=[FakeResult(rowcount=0)])
    result = await user_api_keys_repo.delete_saved_model(
        "u1", "openrouter", "model-x", session=s
    )
    assert result["success"] is False


@pytest.mark.unit
async def test_delete_saved_model_keeps_provider_when_inactive_model_removed():
    # delete model-b; model-a remains and is still the active selection
    s = FakeSession(
        results=[
            FakeResult(rowcount=1),  # delete saved row
            FakeResult(rows=[("model-a",)]),  # remaining models
            FakeResult(scalar="model-a"),  # current active model_selection
        ]
    )
    result = await user_api_keys_repo.delete_saved_model(
        "u1", "openrouter", "model-b", session=s
    )
    assert result == {
        "success": True,
        "provider_removed": False,
        "active_model": "model-a",
    }
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert not any("UPDATE user_api_keys" in q for q in sqls)
    assert not any("DELETE FROM user_api_keys" in q for q in sqls)


@pytest.mark.unit
async def test_delete_saved_model_repoints_active_model():
    # delete the active model-a; model-b remains and becomes active
    s = FakeSession(
        results=[
            FakeResult(rowcount=1),  # delete saved row
            FakeResult(rows=[("model-b",)]),  # remaining models
            FakeResult(scalar="model-a"),  # current active == deleted
            FakeResult(rowcount=1),  # update model_selection
        ]
    )
    result = await user_api_keys_repo.delete_saved_model(
        "u1", "openrouter", "model-a", session=s
    )
    assert result == {
        "success": True,
        "provider_removed": False,
        "active_model": "model-b",
    }
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert any("UPDATE user_api_keys" in q for q in sqls)


@pytest.mark.unit
async def test_delete_saved_model_last_entry_removes_key_row():
    s = FakeSession(
        results=[
            FakeResult(rowcount=1),  # delete saved row
            FakeResult(rows=[]),  # nothing remains
            FakeResult(rowcount=1),  # delete key row
        ]
    )
    result = await user_api_keys_repo.delete_saved_model(
        "u1", "openrouter", "model-a", session=s
    )
    assert result == {"success": True, "provider_removed": True}
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert any("DELETE FROM user_api_keys" in q for q in sqls)


@pytest.mark.unit
async def test_delete_user_api_key_clears_saved_models():
    s = FakeSession()
    result = await user_api_keys_repo.delete_user_api_key("u1", "openrouter", session=s)
    assert result["success"] is True
    sqls = [to_pg_sql(stmt) for stmt in s.executed]
    assert any("DELETE FROM user_api_keys" in q for q in sqls)
    assert any("DELETE FROM user_saved_models" in q for q in sqls)


# ---------------------------------------------------------------------------
# Router: DELETE /api/user/api-keys/{provider} with optional ?model=
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_delete_endpoint_with_model_param_deletes_single_saved_model(
    client, auth_headers
):
    with (
        patch.object(
            user_api_keys_repo,
            "delete_saved_model",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "provider_removed": False,
                    "active_model": "model-b",
                }
            ),
        ) as mock_delete_model,
        patch.object(
            user_api_keys_repo, "delete_user_api_key", new=AsyncMock()
        ) as mock_delete_key,
        patch("api.routers.user.audit_log"),
    ):
        resp = await client.delete(
            "/api/user/api-keys/openrouter",
            params={"model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["provider_removed"] is False
    mock_delete_model.assert_awaited_once()
    args = mock_delete_model.await_args.args
    assert args[1] == "openrouter"
    assert args[2] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    mock_delete_key.assert_not_awaited()


@pytest.mark.unit
async def test_delete_endpoint_without_model_deletes_whole_provider(
    client, auth_headers
):
    with (
        patch.object(
            user_api_keys_repo,
            "delete_user_api_key",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_delete_key,
        patch.object(
            user_api_keys_repo, "delete_saved_model", new=AsyncMock()
        ) as mock_delete_model,
        patch("api.routers.user.audit_log"),
    ):
        resp = await client.delete(
            "/api/user/api-keys/openrouter", headers=auth_headers
        )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_delete_key.assert_awaited_once()
    mock_delete_model.assert_not_awaited()
