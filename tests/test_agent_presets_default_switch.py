"""切換 default preset 的回歸——2026-09-05 線上 P0。

線上實測（EVM_786785，premium）：第二個 preset 按「啟用」→ 500，
asyncpg UniqueViolationError: uq_user_agent_presets_default。
根因：_clear_default 組了 ORM UPDATE 卻沒接 .values()——SQLAlchemy
把它編成「所有欄位綁 None」的 UPDATE，舊 default 沒被清成 false，
set_default 再把新 preset 設成 default 時，撞上「每使用者唯一 default」
的 partial unique index。第一次啟用會過，純粹因為當時沒有舊 default。

兩道防線：
1. 行為測試：set_default 必須先發出「is_default=false」的清舊語句。
2. 守衛測試：全 repo 禁止「ORM update() 沒接 .values()」——這類語句
   編譯成整列綁 None 的 UPDATE，行為與意圖完全無關。
"""

from __future__ import annotations

import ast
import os
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


class _RecordingSession:
    """記錄 execute() 收到的語句，不碰真的 DB。"""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return None

    async def commit(self):
        return None


class TestSetDefaultClearsOldDefault:
    def test_switch_emits_explicit_clear_then_set(self, monkeypatch):
        """清舊 → 設新，兩句都要在，且清舊只動 is_default。"""
        import asyncio

        from core.orm.agent_presets_repo import (
            AgentPresetsRepository,
            agent_presets_repo,
        )

        preset_b = type("Preset", (), {"preset_id": "prst_b", "user_id": "u1"})()
        monkeypatch.setattr(
            AgentPresetsRepository,
            "get_preset",
            AsyncMock(return_value=preset_b),
        )
        db = _RecordingSession()

        ok = asyncio.run(agent_presets_repo.set_default(db, "u1", "prst_b"))

        assert ok is True
        assert len(db.statements) == 2, "必須正好兩句：清舊 + 設新"
        clear, set_new = (s.compile() for s in db.statements)
        assert clear.params.get("is_default") is False, (
            "清舊要把 is_default 綁成 false（線上 P0：沒綏 .values 時它綁 None）"
        )
        assert "name" not in clear.params, (
            "清舊不得把無關欄位綁進 SET——無 values 的 ORM UPDATE 會整列綁 None"
        )
        assert set_new.params.get("is_default") is True


class TestNoValuesLessOrmUpdate:
    """守衛：sqlalchemy.update(ORMEntity) 一律要接 .values()。"""

    _ROOTS = ("core", "api", "scripts")

    def _violations(self) -> list:
        found = []
        for root_dir in self._ROOTS:
            for dirpath, _dirs, files in os.walk(root_dir):
                if "__pycache__" in dirpath:
                    continue
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, fn)
                    tree = ast.parse(open(path, encoding="utf-8").read())
                    if not self._imports_sqlalchemy_update(tree):
                        continue
                    for node in self._orm_update_calls(tree):
                        if not self._chain_has_values(tree, node):
                            found.append(f"{path}:{node.lineno}")
        return found

    @staticmethod
    def _imports_sqlalchemy_update(tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
                if any(a.name == "update" for a in node.names):
                    return True
            elif isinstance(node, ast.Import):
                if any(a.name == "sqlalchemy" for a in node.names):
                    return True
        return False

    @staticmethod
    def _orm_update_calls(tree: ast.Module):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name != "update":
                continue
            arg0 = node.args[0]
            # ORM 實體慣例：大寫開頭的類別名
            if isinstance(arg0, ast.Name) and arg0.id[0].isupper():
                yield node

    @classmethod
    def _chain_has_values(cls, tree: ast.Module, target: ast.Call) -> bool:
        for stmt in (
            s for s in ast.walk(tree)
            if isinstance(s, (ast.Expr, ast.Assign, ast.AnnAssign))
        ):
            contained = any(sub is target for sub in ast.walk(stmt))
            if not contained:
                continue
            return any(
                isinstance(sub, ast.Call)
                and getattr(sub.func, "attr", None) == "values"
                for sub in ast.walk(stmt)
            )
        return True  # update() 當參數傳給其他函式，不在此守衛範圍

    def test_no_orm_update_without_values(self):
        violations = self._violations()
        assert not violations, (
            "這些 sqlalchemy.update(ORMEntity) 沒接 .values()——會編譯成"
            "「整列欄位綁 None」的 UPDATE，行為與意圖無關（2026-09-05 線上 P0"
            "就是這一類）：\n" + "\n".join(violations)
        )
