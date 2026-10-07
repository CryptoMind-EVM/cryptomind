"""alembic 與 runtime reconcile 不得互相絆倒（2026-09-05）。

production 的 alembic_version 卡在 c042 好幾週。失敗鏈：

  1. core/database/schema.py 的 reconcile 用 CREATE TABLE IF NOT EXISTS 在
     app 啟動時建了 user_facts_private
  2. alembic c043 用非冪等的 op.create_table 建同一張表 → DuplicateTable
     → 整個 upgrade 中止，c044 從未執行
  3. reconcile 對**既有**的 user_memory 只做 CREATE TABLE IF NOT EXISTS
     （no-op，不補欄位），然後建引用 agent_id 的索引 → 欄位不存在 → 炸
  4. 而 agent_id 只能靠 c043 加，c043 又被 (2) 擋住

兩套自癒系統互鎖，而 entrypoint 是 best-effort、gunicorn 照常啟動，
服務對外完全健康。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "core/database/schema.py").read_text(encoding="utf-8")
MIGRATIONS = sorted((ROOT / "alembic/versions").glob("*.py"))


def _reconciled_tables() -> set:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))


def _resolve_consts(src: str):
    const = dict(re.findall(r'^(_[A-Z_]+)\s*=\s*["\'](\w+)["\']', src, re.M))
    return lambda x: const.get(x.strip().strip("\"'"), x.strip().strip("\"'"))


class TestNoDuplicateTableCollision:
    def test_no_migration_creates_a_table_reconcile_also_creates(self):
        """兩邊都建同一張表 → 誰先跑誰贏，後跑的整個 upgrade 中止。"""
        reconciled = _reconciled_tables()
        clashes = []
        for f in MIGRATIONS:
            src = f.read_text(encoding="utf-8")
            for t in re.findall(r'op\.create_table\(\s*["\'](\w+)["\']', src):
                if t in reconciled:
                    clashes.append(f"{f.name}: op.create_table({t!r})")
        assert not clashes, (
            "這些遷移用非冪等的 op.create_table 建了 reconcile 也會建的表——"
            "reconcile 先跑就 DuplicateTable：\n  " + "\n  ".join(clashes)
            + "\n改用 op.execute('CREATE TABLE IF NOT EXISTS ...')。"
        )


class TestReconcileCanEvolveExistingTables:
    def test_indexed_columns_added_by_migrations_are_also_added_by_reconcile(self):
        """reconcile 建索引前要自己補得起欄位。

        CREATE TABLE IF NOT EXISTS 對既有表是 no-op，欄位不會出現；索引引用
        它就炸，整個 reconcile step 掛掉。
        """
        added = {}
        for f in MIGRATIONS:
            src = f.read_text(encoding="utf-8")
            resolve = _resolve_consts(src)
            for t, c in re.findall(
                r'op\.add_column\(\s*([^,]+),\s*sa\.Column\(\s*["\'](\w+)["\']', src
            ):
                added[(resolve(t), c)] = f.name
            # raw SQL 版本（含 f-string 變數表名）。2026-09-05：c043 從
            # op.add_column 改成 op.execute 之後，只認 op.add_column 的掃描
            # 就瞎了——修 code 的同時把守衛弄瞎，是這份檔案要防的同一件事。
            for t, c in re.findall(
                r"ALTER TABLE \{?([\w_]+)\}? ADD COLUMN(?: IF NOT EXISTS)? (\w+)", src
            ):
                added[(resolve(t), c)] = f.name

        missing = []
        for m in re.finditer(
            r'CREATE INDEX IF NOT EXISTS (\w+)\s*"?\s*\n?\s*"?ON (\w+)\(([^)]*)\)', SCHEMA
        ):
            idx, tbl, cols = m.group(1), m.group(2), m.group(3)
            for col in (x.strip() for x in cols.split(",")):
                if (tbl, col) not in added:
                    continue
                # reconcile 必須自己 ADD COLUMN IF NOT EXISTS
                guard = f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col}"
                if guard not in SCHEMA:
                    missing.append(f"{idx} 引用 {tbl}.{col}（欄位來自 {added[(tbl, col)]}）")
        assert not missing, (
            "reconcile 建的索引引用了「只有遷移會加」的欄位，但沒有先"
            " ADD COLUMN IF NOT EXISTS——舊 DB 上必炸：\n  " + "\n  ".join(missing)
        )


class TestSchemaDriftIsVisible:
    """卡住本身不可怕，卡住而沒人知道才可怕。"""

    def test_startup_reports_schema_version(self):
        from core.feature_flags import schema_version_line

        line = schema_version_line()
        assert line.startswith("[SchemaVersion]") or "SchemaDrift" in line

    def test_drift_is_a_warning_not_an_info(self):
        src = (ROOT / "core/feature_flags.py").read_text(encoding="utf-8")
        assert 'log.warning if "SchemaDrift" in line' in src, (
            "落後要走 warning——埋在 info 裡跟沒有一樣（production 卡 c042 "
            "好幾週就是這樣過去的）"
        )
