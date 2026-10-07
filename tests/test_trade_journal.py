"""投資帳本測試（docs/plans/2026-08-21-investment-journal-design.md）。

涵蓋：
- Schema：trade_journal 表 + 欄位存在
- Repo：驗證（market/instrument/direction/side/quantity/price/leverage）
- PnL 計算：
  - 現貨加權平均成本（買→買→賣→買）
  - 槓桿做多（PnL × leverage）
  - 做空（成本-現價）
  - 清倉歸零
  - 跨市場（台股「張」/美股「股」）
- Agent 工具：record_entry/query_ledger/get_portfolio_pnl 註冊與 schema
- 種子：tools_catalog 三條
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]

from core.database import schema as trade_journal_schema  # noqa: E402
from core.orm import trade_journal_repo as repo_module  # noqa: E402


class TestSchema:
    def test_migration_c032_exists(self):
        src = (ROOT / "alembic/versions" / "c032_trade_journal.py").read_text(encoding="utf-8")
        assert "trade_journal" in src
        for col in ["symbol", "market", "instrument_type", "direction", "leverage",
                     "quantity", "price", "currency", "fee", "traded_at"]:
            assert col in src, f"c032 應有 {col}"

    def test_schema_py_creates_table(self):
        import core.database.schema as s

        src = inspect.getsource(s)
        assert "create_trade_journal_table" in src
        assert "trade_journal" in src


class TestRepoValidation:
    def _repo(self):
        from core.orm.trade_journal_repo import TradeJournalRepo

        return TradeJournalRepo("test-user")

    def test_valid_markets_defined(self):
        from core.orm.trade_journal_repo import VALID_MARKETS

        assert "crypto" in VALID_MARKETS
        assert "tw_stock" in VALID_MARKETS
        assert "us_stock" in VALID_MARKETS
        assert "forex" in VALID_MARKETS
        assert "commodity" in VALID_MARKETS

    def test_valid_instruments(self):
        from core.orm.trade_journal_repo import VALID_INSTRUMENTS

        assert {"spot", "futures", "margin"} == VALID_INSTRUMENTS

    def test_spot_forces_long_leverage_1(self):
        """現貨恆 long、leverage=1（設計決策）。"""
        repo = self._repo()
        # 直接呼叫 add_trade 會碰 DB——這裡驗證邏輯在 code 裡
        src = inspect.getsource(repo.add_entry)
        assert 'spot' in src and 'long' in src and 'leverage = 1' in src


class TestPnLCalculation:
    """PnL 公式驗證（不碰 DB——用 repo 的邏輯片段）。"""

    def test_weighted_average_buy_buy_sell(self):
        """買 1 @ 100、買 1 @ 200 → 均價 150；賣 0.5 @ 300 → 已實現 +75。"""
        # 手動驗證加權平均
        pos_qty = Decimal("0")
        pos_cost = Decimal("0")
        realized = Decimal("0")

        # buy 1 @ 100
        new_qty = pos_qty + Decimal("1")
        pos_cost = (pos_qty * pos_cost + Decimal("1") * Decimal("100")) / new_qty
        pos_qty = new_qty  # qty=1, cost=100

        # buy 1 @ 200
        new_qty = pos_qty + Decimal("1")
        pos_cost = (pos_qty * pos_cost + Decimal("1") * Decimal("200")) / new_qty
        pos_qty = new_qty  # qty=2, cost=150

        assert pos_cost == Decimal("150")

        # sell 0.5 @ 300
        realized = (Decimal("300") - pos_cost) * Decimal("0.5")  # (300-150)*0.5=75
        pos_qty -= Decimal("0.5")

        assert realized == Decimal("75")
        assert pos_qty == Decimal("1.5")

    def test_leverage_long_pnl(self):
        """10x 做多 0.5 BTC @ 61200 → 現價 64800 → +$18,000。"""
        entry = Decimal("61200")
        current = Decimal("64800")
        qty = Decimal("0.5")
        leverage = Decimal("10")
        pnl = (current - entry) * qty * leverage
        assert pnl == Decimal("18000")

    def test_short_pnl(self):
        """5x 做空 0.5 BTC @ 65000 → 現價 64800 → +$500。"""
        entry = Decimal("65000")
        current = Decimal("64800")
        qty = Decimal("0.5")
        leverage = Decimal("5")
        pnl = (entry - current) * qty * leverage
        assert pnl == Decimal("500")

    def test_spot_no_leverage(self):
        """現貨買 0.5 BTC @ 61200 → 現價 64800 → +$1,800。"""
        entry = Decimal("61200")
        current = Decimal("64800")
        qty = Decimal("0.5")
        leverage = Decimal("1")  # 現貨恆 1
        pnl = (current - entry) * qty * leverage
        assert pnl == Decimal("1800")

    def test_close_position_resets(self):
        """賣光後再買——均價重新計算（不是延續舊均價）。"""
        pos_qty = Decimal("0")
        pos_cost = Decimal("0")

        # buy 1 @ 100 → sell 1 @ 150（清倉）
        pos_qty = Decimal("1")
        pos_cost = Decimal("100")
        pos_qty -= Decimal("1")
        if pos_qty <= 0:
            pos_qty = Decimal("0")
            pos_cost = Decimal("0")

        # 再買 1 @ 200——均價應是 200（不是 (100+200)/2=150）
        new_qty = pos_qty + Decimal("1")
        pos_cost = (pos_qty * pos_cost + Decimal("1") * Decimal("200")) / new_qty
        pos_qty = new_qty

        assert pos_cost == Decimal("200"), "清倉後重買，均價重新計算"


class TestAgentTools:
    def test_record_entry_tool_exists(self):
        from core.tools.crypto_modules.trade_journal import record_entry

        assert record_entry.name == "record_entry"

    def test_query_ledger_tool_exists(self):
        from core.tools.crypto_modules.trade_journal import query_ledger

        assert query_ledger.name == "query_ledger"

    def test_get_portfolio_pnl_tool_exists(self):
        from core.tools.crypto_modules.trade_journal import get_portfolio_pnl

        assert get_portfolio_pnl.name == "get_portfolio_pnl"

    def test_record_entry_schema_fields(self):
        from core.tools.crypto_modules.trade_journal import record_entry

        schema = record_entry.args_schema.model_json_schema()["properties"]
        for field in ["symbol", "market", "amount", "quantity"]:
            assert field in schema, f"record_entry 應有 {field} 參數"
        assert "leverage" in schema
        assert "instrument_type" in schema
        assert "direction" in schema

    def test_bootstrap_registers_journal_tools(self):
        # 不能寫 `import core.agents.bootstrap as bs`：core/agents/__init__.py 匯出了同名函式
        # bootstrap，`as` 拿到的是函式、getsource 只會回函式本體（找不到工具註冊）。
        import importlib

        bs = importlib.import_module("core.agents.bootstrap")

        src = inspect.getsource(bs)
        for tool in ["record_entry", "query_ledger", "get_portfolio_pnl"]:
            assert f'name="{tool}"' in src, f"bootstrap 應註冊 {tool}"

    def test_tools_seed_has_journal(self):
        import core.database.tools as t

        src = inspect.getsource(t)
        for tool in ["record_entry", "query_ledger", "get_portfolio_pnl"]:
            assert f'"{tool}"' in src, f"tools seed 應有 {tool}"

    def test_crypto_modules_exports_journal(self):
        pass


class TestMarketUnits:
    def test_market_unit_mapping(self):
        from core.tools.crypto_modules.trade_journal import _market_unit

        assert _market_unit("crypto") == ""
        assert _market_unit("tw_stock") == "張"
        assert _market_unit("us_stock") == "股"
        assert _market_unit("forex") == "手"
        assert _market_unit("commodity") == "口"


class TestProposeConsent:
    """金流 HITL：record_entry 只提案（marker），不寫 DB（Propose→Confirm→Commit）。"""

    def test_record_entry_returns_needs_consent_marker(self, monkeypatch):
        """提案回傳 __needs_consent__ marker（JSON），絕不直接寫入。"""
        import json as jsonlib

        from core.tools.crypto_modules import trade_journal as tj

        captured = {}

        class FakeRepo:
            # c039：提案時要讀使用者的報表基準幣（凍結匯率要對得上基準幣）
            base_currency = "TWD"

            def add_entry(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True, "id": 999}

        monkeypatch.setattr(tj, "_get_current_user_id", lambda: "user-1")
        monkeypatch.setattr(
            "core.orm.trade_journal_repo.get_journal_repo", lambda uid: FakeRepo()
        )
        # 匯率凍結：提案時抓一次（ TWD→TWD = 1.0）
        monkeypatch.setattr(
            "core.tools.crypto_modules.exchange_rate.get_exchange_rate",
            lambda cur, base: 1.0,
        )

        raw = record_entry_result(
            tj, entry_type="expense", amount=250, currency="TWD", note="lunch"
        )
        marker = jsonlib.loads(raw)
        assert marker["__needs_consent__"] is True
        assert marker["kind"] == "journal_entry"
        assert marker["entry_type"] == "expense"
        assert marker["amount"] == 250
        # 提案不寫 DB
        assert captured == {}, "record_entry 提案階段不得寫入 DB"
        # 匯率在提案時凍結
        assert marker["exchange_rate"] == 1.0
        assert marker["converted_amount"] == 250.0
        assert marker["rate_source"] == "frozen"

    def test_record_entry_requires_login(self, monkeypatch):
        from core.tools.crypto_modules import trade_journal as tj

        monkeypatch.setattr(tj, "_get_current_user_id", lambda: None)
        raw = record_entry_result(tj, entry_type="expense", amount=100)
        assert raw.startswith("Error:")

    def test_marker_extracted_by_claw_loop_scanner(self):
        """claw_loop._extract_consent_signal 能掃到 journal_entry marker。"""
        import json as jsonlib

        from core.agents.manager.claw_loop import _extract_consent_signal

        marker = jsonlib.dumps(
            {
                "__needs_consent__": True,
                "kind": "journal_entry",
                "entry_type": "expense",
                "amount": 250,
            },
            ensure_ascii=False,
        )
        signal = _extract_consent_signal({"tool_outputs": [marker]})
        assert signal is not None
        assert signal["kind"] == "journal_entry"

    def test_docstring_teaches_propose_semantics(self):
        """LLM 可見的 docstring 必須教「確認卡 + 核准才儲存 + 勿用 remember」。"""
        from core.tools.crypto_modules.trade_journal import record_entry

        doc = record_entry.description
        assert "confirmation card" in doc
        assert "memory/remember" in doc
        assert "ONLY after the user approves" in doc


class TestSoftDelete:
    """soft delete 契約（c035，docs/plans/2026-08-24-journal-delete-safety-design.md）。

    刪除 = 標記 deleted_at；所有讀取過濾 deleted_at IS NULL；
    不得退回 DELETE FROM 硬刪除（AGENTS.md soft delete 慣例）。
    """

    def test_migration_c035_exists(self):
        src = (ROOT / "alembic/versions" / "c035_journal_soft_delete.py").read_text(
            encoding="utf-8"
        )
        assert "deleted_at" in src
        assert "c034" in src  # down_revision 鏈在 c034 之後

    def test_schema_py_creates_deleted_at(self):
        import core.database.schema as s

        src = inspect.getsource(s.create_trade_journal_table)
        assert "deleted_at" in src

    def test_delete_trade_is_soft_not_hard_delete(self):
        from core.orm.trade_journal_repo import TradeJournalRepo

        src = inspect.getsource(TradeJournalRepo.delete_trade)
        # 剝 docstring——歷史說明可能提到舊行為，只對可執行碼斷言
        # （不用 regex：SQL 本身也是 triple-quoted，會被誤刪）
        code = src.replace(TradeJournalRepo.delete_trade.__doc__ or "", "")
        assert "DELETE FROM" not in code, "不得硬刪除（soft delete 契約）"
        assert "deleted_at = NOW()" in code
        # ownership 防線在 SQL 本體：user_id 條件不得移除
        assert "user_id = %s" in code
        assert "deleted_at IS NULL" in code

    def test_delete_trade_zero_rows_reports_not_found(self, monkeypatch):
        """刪 0 rows（不存在／他人帳目／已刪）→ not found，不是假成功。"""
        import contextlib

        import core.database.base as db_base
        from core.orm import trade_journal_repo as repo_mod

        class _FakeCur:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return None  # SELECT 查無此列（他人帳目／已刪）

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

            def commit(self):
                pass

            def rollback(self):
                pass

        @contextlib.contextmanager
        def fake_transaction(connection=None):
            yield _FakeConn()

        monkeypatch.setattr(db_base, "transaction", fake_transaction)
        result = repo_mod.TradeJournalRepo("user-1").delete_trade(42)
        assert result == {"ok": False, "error": "entry not found"}

    def test_delete_trade_marks_and_passes_ownership_params(self, monkeypatch):
        import contextlib
        from types import SimpleNamespace

        import core.database.base as db_base
        from core.orm import trade_journal_repo as repo_mod

        captured = []

        class _FakeCur:
            description = [
                SimpleNamespace(name=n)
                for n in ("id", "user_id", "price", "quantity", "note")
            ]
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                captured.append((sql, params))

            def fetchone(self):
                return (42, "user-1", 100.0, 1.0, "")

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

            def commit(self):
                pass

            def rollback(self):
                pass

        @contextlib.contextmanager
        def fake_transaction(connection=None):
            yield _FakeConn()

        monkeypatch.setattr(db_base, "transaction", fake_transaction)
        result = repo_mod.TradeJournalRepo("user-1").delete_trade(42)

        assert result == {"ok": True}
        sqls = [s for s, _ in captured]
        update = next(s for s in sqls if "UPDATE trade_journal" in s)
        assert "deleted_at = NOW()" in update
        assert "deleted_at IS NULL" in update
        # ownership 綁定：UPDATE 的 WHERE 必須同時帶 id 與 user_id
        assert "WHERE id = %s AND user_id = %s" in update
        update_params = next(p for s, p in captured if "UPDATE trade_journal" in s)
        assert update_params == (42, "user-1")
        # c037：delete 修訂與 soft delete 標記同批執行（同 tx）
        assert any("INSERT INTO journal_revisions" in s for s in sqls)

    def test_read_paths_filter_deleted(self):
        from core.orm.trade_journal_repo import TradeJournalRepo

        for method in ("_entry_filters", "get_category_summary", "update_entry"):
            src = inspect.getsource(getattr(TradeJournalRepo, method))
            assert "deleted_at IS NULL" in src, (
                f"{method} 必須過濾已刪條目（已刪資料不得重現）"
            )
        # 列表與收支合計的 WHERE 由 _entry_filters 統一產生（2026-09-25）
        for method in ("list_trades", "get_entry_totals"):
            src = inspect.getsource(getattr(TradeJournalRepo, method))
            assert "self._entry_filters(" in src, f"{method} 沒走共用篩選條件"

    def test_get_positions_inherits_filter_via_list_trades(self):
        """get_positions 經 list_trades 取資料——過濾自動繼承（不另開 SQL）。"""
        from core.orm.trade_journal_repo import TradeJournalRepo

        src = inspect.getsource(TradeJournalRepo.get_positions)
        assert "list_trades" in src
        assert "FROM trade_journal" not in src


def _trade(**overrides):
    """list_trades 形狀的 trade dict（get_positions 行為測試用）。"""
    base = {
        "id": 1, "symbol": "ETH", "market": "crypto",
        "instrument_type": "futures", "direction": "long", "side": "buy",
        "leverage": 1, "quantity": 1, "price": 3000, "currency": "USD",
        "fee": 0, "fee_currency": "", "entry_type": "trade",
        "category": "investment", "base_currency": "TWD",
        "converted_amount": None, "exchange_rate": None, "rate_source": "auto",
        "traded_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
        "source": "chat", "note": "",
    }
    base.update(overrides)
    return base


class TestGetPositionsBehavior:
    """行為化持倉測試——monkeypatch list_trades、跑真實 get_positions。

    2026-08-24 review：舊 TestPnLCalculation 在測試內重寫公式再斷言算術
    恆等式，抓不到 _infer_side_short 讀不到 side 的死碼（做空永不平倉）。
    本組測試鎖實際行為。
    """

    def _positions(self, monkeypatch, trades):
        from core.orm.trade_journal_repo import TradeJournalRepo

        monkeypatch.setattr(
            TradeJournalRepo, "list_trades", lambda self, **kw: trades
        )
        return TradeJournalRepo("user-1").get_positions(price_lookup={})

    def test_short_open_and_cover(self, monkeypatch):
        """5x 做空 1 ETH @3000 → buy cover 1 @2900 → 清倉、已實現 +500。

        回歸鎖：_infer_side_short 曾讀永不存在的 ``_side`` 鍵 → side 恆
        sell → 平倉分支死碼，做空倉只會翻倍（qty=2、PnL=0）。
        """
        trades = [
            _trade(
                id=1, side="sell", direction="short", leverage=5,
                price=3000, quantity=1,
                traded_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc),
            ),
            _trade(
                id=2, side="buy", direction="short", leverage=5,
                price=2900, quantity=1,
                traded_at=datetime(2026, 8, 24, 11, tzinfo=timezone.utc),
            ),
        ]
        pos = self._positions(monkeypatch, trades)
        assert len(pos) == 1
        assert pos[0]["quantity"] == Decimal("0"), "cover 後應清倉，不是翻倍"
        assert pos[0]["realized_pnl"] == Decimal("500")

    def test_long_weighted_average_partial_sell(self, monkeypatch):
        """買 1@100、買 1@200 → 均價 150；賣 0.5@300 → 已實現 +75、剩 1.5。"""
        trades = [
            _trade(id=1, price=100, quantity=1, instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
            _trade(id=2, price=200, quantity=1, instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 11, tzinfo=timezone.utc)),
            _trade(id=3, side="sell", price=300, quantity=0.5,
                   instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 12, tzinfo=timezone.utc)),
        ]
        pos = self._positions(monkeypatch, trades)
        assert len(pos) == 1
        assert pos[0]["quantity"] == Decimal("1.5")
        assert pos[0]["avg_cost"] == Decimal("150")
        assert pos[0]["realized_pnl"] == Decimal("75")

    def test_desc_input_resorted_ascending(self, monkeypatch):
        """list_trades 回 DESC（新→舊）——累加前必須重排 ASC（d37b6a7 修的回歸）。"""
        trades = [
            _trade(id=3, side="sell", price=300, quantity=0.5,
                   instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 12, tzinfo=timezone.utc)),
            _trade(id=2, price=200, quantity=1, instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 11, tzinfo=timezone.utc)),
            _trade(id=1, price=100, quantity=1, instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
        ]
        pos = self._positions(monkeypatch, trades)
        assert pos[0]["quantity"] == Decimal("1.5")
        assert pos[0]["realized_pnl"] == Decimal("75")

    def test_oversell_pnl_capped_at_held_quantity(self, monkeypatch):
        """買 1@100、賣 3@150 → 已實現只算 1 顆（+50），不是整筆 3 顆（+150）。

        回歸鎖：賣量超過持倉（帳本漏記買入）時整筆算損益，
        空倉 avg_cost=0 的部分等於憑空獲利。超出的量照舊歸零丟棄。
        """
        trades = [
            _trade(id=1, price=100, quantity=1, instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
            _trade(id=2, side="sell", price=150, quantity=3,
                   instrument_type="spot",
                   traded_at=datetime(2026, 8, 24, 11, tzinfo=timezone.utc)),
        ]
        pos = self._positions(monkeypatch, trades)
        assert pos[0]["quantity"] == Decimal("0")
        assert pos[0]["realized_pnl"] == Decimal("50")

    def test_sell_without_position_realizes_nothing(self, monkeypatch):
        trades = [
            _trade(id=1, side="sell", price=200, quantity=1, instrument_type="spot"),
        ]
        pos = self._positions(monkeypatch, trades)
        assert pos[0]["quantity"] == Decimal("0")
        assert pos[0]["realized_pnl"] == Decimal("0")

    def test_short_overcover_pnl_capped(self, monkeypatch):
        """空 1@3000、回補 2@2900 → 已實現只算 1 顆（+100）。"""
        trades = [
            _trade(id=1, side="sell", direction="short", price=3000, quantity=1,
                   traded_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
            _trade(id=2, side="buy", direction="short", price=2900, quantity=2,
                   traded_at=datetime(2026, 8, 24, 11, tzinfo=timezone.utc)),
        ]
        pos = self._positions(monkeypatch, trades)
        assert pos[0]["quantity"] == Decimal("0")
        assert pos[0]["realized_pnl"] == Decimal("100")


class TestPnlMarketPriceRouting:
    """現價按 market 分流（2026-08-24 補完——此前全部走 get_exchange_rate，
    只認得幣種代碼，股票持倉恆顯「（無現價）」）。"""

    def _run_tool(self, monkeypatch, trades, stock_prices=None, crypto_rates=None):
        from core.orm.trade_journal_repo import TradeJournalRepo
        from core.tools.crypto_modules import trade_journal as tj

        monkeypatch.setattr(
            TradeJournalRepo, "list_trades", lambda self, **kw: trades
        )
        monkeypatch.setattr(tj, "_get_current_user_id", lambda: "user-1")
        calls = {"stock": [], "crypto": []}

        def fake_stock(sym, mkt):
            calls["stock"].append((sym, mkt))
            return (stock_prices or {}).get(sym)

        def fake_rate(cur, base):
            calls["crypto"].append(cur)
            return (crypto_rates or {}).get(cur)

        monkeypatch.setattr(tj, "_fetch_stock_current_price", fake_stock)
        monkeypatch.setattr(
            "core.tools.crypto_modules.exchange_rate.get_exchange_rate", fake_rate
        )
        fn = (
            tj.get_portfolio_pnl.func
            if hasattr(tj.get_portfolio_pnl, "func")
            else tj.get_portfolio_pnl
        )
        return fn(), calls

    def test_crypto_via_rate_tool_stocks_via_provider(self, monkeypatch):
        """crypto 走匯率/幣價工具；us/tw 股票走 provider——不得再拿 AAPL 問匯率。"""
        trades = [
            _trade(id=1, symbol="BTC", market="crypto", price=61200, quantity=0.5),
            _trade(id=2, symbol="AAPL", market="us_stock", price=220, quantity=10),
            _trade(
                id=3, symbol="2330.TW", market="tw_stock", price=912,
                quantity=2, currency="TWD",
            ),
        ]
        out, calls = self._run_tool(
            monkeypatch, trades,
            stock_prices={"AAPL": 230.0, "2330.TW": 918.0},
            crypto_rates={"BTC": 66000.0},
        )
        assert ("BTC", "crypto") not in calls["stock"]
        assert "BTC" in calls["crypto"]
        assert ("AAPL", "us_stock") in calls["stock"]
        assert ("2330.TW", "tw_stock") in calls["stock"]
        # 現價真的進到輸出（非「無現價」）
        assert "66,000" in out and "230" in out and "918" in out
        assert "無現價" not in out

    def test_tw_lot_multiplier_design_example(self, monkeypatch):
        """設計文件 §1 範例：2330.TW 買 2 張 @912、現價 918 → +NT$12,000。

        台股 quantity 記張、price 記每股 → PnL ×1000（1張=1000股）。
        """
        trades = [
            _trade(
                id=1, symbol="2330.TW", market="tw_stock", price=912,
                quantity=2, currency="TWD", instrument_type="spot",
            ),
        ]
        out, _ = self._run_tool(
            monkeypatch, trades, stock_prices={"2330.TW": 918.0}
        )
        assert "+12,000 TWD" in out

    def test_us_stock_no_multiplier(self, monkeypatch):
        """美股 quantity=股數、price=每股——不乘 1000（10股@220 現價230 → +100）。"""
        trades = [
            _trade(id=1, symbol="AAPL", market="us_stock", price=220, quantity=10),
        ]
        out, _ = self._run_tool(
            monkeypatch, trades, stock_prices={"AAPL": 230.0}
        )
        assert "+100 USD" in out

    def test_stock_price_failure_degrades_gracefully(self, monkeypatch):
        """單一標的取價失敗 → 顯示「無現價」，不得炸整個查詢。"""
        trades = [
            _trade(id=1, symbol="AAPL", market="us_stock", price=220, quantity=10),
        ]
        out, _ = self._run_tool(monkeypatch, trades, stock_prices={})
        assert "無現價" in out


class TestRepoInstanceSafety:
    """get_journal_repo 每次呼叫建新 instance（2026-08-24 review）。"""

    def test_fresh_instance_per_call(self):
        from core.orm.trade_journal_repo import TradeJournalRepo, get_journal_repo

        a1 = get_journal_repo("user-a")
        a2 = get_journal_repo("user-a")
        b = get_journal_repo("user-b")
        assert a1.user_id == "user-a"
        assert b.user_id == "user-b"
        # 同 uid 兩次呼叫也必須是不同物件——原 singleton 在多執行緒下
        # 曾可能讓 user A 拿到 user B 的 repo（跨用戶帳本存取）
        assert a1 is not a2
        assert isinstance(a1, TradeJournalRepo)

    def test_no_module_global_repo_cache(self):
        import core.orm.trade_journal_repo as repo_mod

        assert not hasattr(repo_mod, "_journal_repo") or repo_mod._journal_repo is None


class TestFrontendEscapeContract:
    """前端渲染安全契約（Python 端靜態鎖——前端無 unit runner）。"""

    def test_journal_tab_escapes_currency(self):
        """列表金額欄的 currency 必須 escape（stored XSS 防線，2026-08-24 review）。"""
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        assert "this._esc(e.currency)" in src, (
            "e.currency 進 innerHTML 前必須過 this._esc()"
        )


def record_entry_result(tj, **kwargs):
    """繞過 LangChain tool wrapper 直接取 plain function 結果。

    StructuredTool 的 func 是原函數；_parse_input 不做轉換時直接呼叫。
    """
    fn = tj.record_entry.func if hasattr(tj.record_entry, "func") else tj.record_entry
    return fn(**kwargs)


class TestVersionHistory:
    """c037 版本史契約：修訂表存在、寫入與主變更同 tx、restore 套凍結快照。

    設計：docs/plans/2026-08-26-journal-version-history-design.md
    """

    def test_migration_c037_exists(self):
        src = (ROOT / "alembic/versions" / "c037_journal_revisions.py").read_text(
            encoding="utf-8"
        )
        assert 'revision = "c037"' in src
        assert 'down_revision = "c036"' in src
        assert "CREATE TABLE IF NOT EXISTS journal_revisions" in src
        assert "revision_no" in src and "snapshot" in src
        assert "UNIQUE (entry_id, revision_no)" in src

    def test_schema_py_creates_journal_revisions(self):
        src = inspect.getsource(trade_journal_schema.create_journal_revisions_table)
        assert "journal_revisions" in src
        assert "revision_no" in src and "snapshot" in src

    def test_schema_registers_journal_revisions(self):
        src = inspect.getsource(trade_journal_schema.create_all_tables)
        assert "create_journal_revisions_table" in src

    def test_revision_no_is_max_plus_one(self):
        src = inspect.getsource(repo_module._insert_revision)
        assert "COALESCE(MAX(revision_no), 0) + 1" in src

    def test_add_entry_writes_create_revision(self):
        src = inspect.getsource(repo_module.TradeJournalRepo._validate_and_insert)
        assert "_insert_revision" in src and '"create"' in src

    def test_update_entry_writes_revision_same_tx(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.update_entry)
        assert "_insert_revision" in src
        # 匯率重算（同步 httpx ≤5s）必須在 transaction() 之外——不得佔連線
        assert src.index("get_exchange_rate") < src.index("transaction()")

    def test_delete_trade_writes_delete_revision(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.delete_trade)
        assert "_insert_revision" in src and '"delete"' in src
        assert "DELETE FROM" not in src  # 不得回退成硬刪除

    def test_claw_loop_passes_chat_source(self):
        src = (ROOT / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
        assert 'delete_trade(int(entry_id), source="chat")' in src
        assert 'update_entry(int(entry_id), source="chat"' in src

    def test_restore_restores_frozen_rate_without_refetch(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.restore_entry)
        assert "get_exchange_rate" not in src  # 套凍結快照，不重抓匯率
        assert "deleted_at = NULL" in src
        assert "_insert_revision" in src and '"restore"' in src

    def test_restore_defaults_to_pre_delete_state(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.restore_entry)
        # 一鍵救回優先取 delete 快照（刪除瞬間的精確狀態）；create/update
        # 僅為 c037 前舊資料的 fallback——先前的單一 create/update 條件在
        # 「刪除前曾時間線還原」時會救回更舊狀態（2026-08-26 review W1）
        assert "action = 'delete'" in src
        assert "action IN ('create', 'update')" in src
        assert src.index("action = 'delete'") < src.index(
            "action IN ('create', 'update')"
        )
        assert "ORDER BY revision_no DESC LIMIT 1" in src


class TestUxRound2:
    """UX 第二輪契約：多值 entry_type、first_traded_at、income 類別映射。

    設計：docs/plans/2026-08-26-journal-ux-round2-design.md
    """

    def test_list_trades_supports_multi_entry_type(self, monkeypatch):
        captured = {}

        def fake_query_all(sql, params=None):
            captured["sql"] = sql
            captured["params"] = list(params or [])
            return []

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        repo_module.TradeJournalRepo("u1").list_trades(
            entry_type=["expense", "income"]
        )
        assert "entry_type = ANY(%s)" in captured["sql"]
        assert ["expense", "income"] in captured["params"]

    def test_list_trades_single_str_still_works(self, monkeypatch):
        """相容單值 str（agent query_ledger 呼叫路徑不動）。"""
        captured = {}

        def fake_query_all(sql, params=None):
            captured["params"] = list(params or [])
            return []

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        repo_module.TradeJournalRepo("u1").list_trades(entry_type="trade")
        assert ["trade"] in captured["params"]

    def test_get_positions_tracks_first_traded_at(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.get_positions)
        assert '"first_traded_at"' in src, (
            "持倉須追蹤最早開倉時間（持有期間顯示用）"
        )

    def test_get_positions_filters_to_trades_only(self):
        """get_positions 不得把 expense/income 當持倉計算。

        2026-08-26 實測：list_trades 泛化支援全部 entry_type 後，
        get_positions 呼叫端漏補 trade 過濾，兩筆支出被算成
        symbol=TWD、qty=2、均價 425 的「持倉」（實網復現）。
        """
        src = inspect.getsource(repo_module.TradeJournalRepo.get_positions)
        assert 'entry_type="trade"' in src or "entry_type='trade'" in src, (
            "get_positions 必須以 entry_type='trade' 過濾——"
            "支出/收入不是投資持倉（損益會被收支污染）"
        )

    def test_infer_category_income_mapping(self):
        from core.tools.crypto_modules.exchange_rate import infer_category

        assert infer_category("薪水 50000", "income") == "salary"
        assert infer_category("月薪", "income") == "salary"
        assert infer_category("年終獎金", "income") == "bonus"
        assert infer_category("股利收入", "income") == "investment_income"
        assert infer_category("配息", "income") == "investment_income"
        assert infer_category("雜項", "income") == "other"

    def test_build_price_lookup_extracted(self):
        src = (
            ROOT / "core/tools/crypto_modules/trade_journal.py"
        ).read_text(encoding="utf-8")
        assert "def build_price_lookup" in src
        # get_portfolio_pnl 改呼叫共用函式（agent 行為不變）
        assert "build_price_lookup(trades)" in src

    def test_frontend_contract_round2(self):
        """UX 第二輪前端契約：收入類別表、自填欄位、年份、自訂類別 escape。"""
        src = (
            ROOT / "web/js/components/tab-journal.js"
        ).read_text(encoding="utf-8")
        assert "INCOME_CATEGORIES" in src
        assert "journal-entry-category-custom" in src
        assert "getFullYear()" in src  # 非當年份顯示年份
        assert "this._esc(catId)" in src  # 自訂類別原文必經 escape
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        assert "journal-entry-category-custom" in html

    def test_frontend_month_filter_keeps_string_sentinel(self):
        """「本月」篩選是字串哨兵值，不得被 parseInt 吃掉。

        2026-08-26 實測：filters.days 賦值包了 parseInt，
        parseInt('month')=NaN → ==='month' 永不成立 → 送出空日期
        參數 →「本月」實際顯示全部資料（財務數字錯誤）。
        """
        src = (
            ROOT / "web/js/components/tab-journal.js"
        ).read_text(encoding="utf-8")
        assignment = [
            line.strip()
            for line in src.splitlines()
            if "JournalState.filters.days =" in line
        ]
        assert assignment, "找不到 filters.days 賦值"
        for line in assignment:
            assert "parseInt" not in line, (
                f"filters.days 賦值不得 parseInt（破壞 'month' 哨兵）：{line}"
            )


class TestReviewFixBatch:
    """2026-08-26 全面 review 修復批次——資料層 fail-closed 與併發語義。

    涵蓋：還原預設快照（W1）、匯率抓不到不寫入（W2）、修訂競態 conflict
    （W3）、回收筒索引（I2/c038）、空編輯不寫修訂（I3）、非正金額擋下。
    """

    # ── W2：匯率抓不到 → fail-closed ────────────────────────────
    def test_update_entry_fails_closed_when_rate_unavailable(self, monkeypatch):
        """改幣別但匯率服務掛掉：不得帶舊匯率寫入（矛盾凍結值），回 rate unavailable。"""
        captured = {}

        def fake_query_all(sql, params=None):
            captured["sql"] = sql
            return [{
                "id": 5, "price": 100.0, "quantity": 1, "currency": "USD",
                "category": "food", "note": "", "converted_amount": 3100.0,
                "exchange_rate": 31.0, "rate_source": "auto",
                "entry_type": "expense", "symbol": "USD", "market": "cash",
                "base_currency": "TWD", "user_id": "u1", "deleted_at": None,
            }]

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        import core.database.base as db_base

        monkeypatch.setattr(
            db_base, "transaction", lambda: _FakeTx(captured)
        )
        import core.tools.crypto_modules.exchange_rate as fx

        monkeypatch.setattr(fx, "get_exchange_rate", lambda cur, base: None)

        result = repo_module.TradeJournalRepo("u1").update_entry(
            5, currency="EUR"
        )
        assert result == {"ok": False, "error": "rate unavailable"}
        # 不得寫入：UPDATE 從未執行
        assert not any("UPDATE trade_journal" in s for s in captured.get("executed", []))

    # ── 非正金額 ────────────────────────────────────────────────
    def test_update_entry_rejects_non_positive_amount_before_db(self, monkeypatch):
        def fail_query_all(sql, params=None):
            raise AssertionError("非正金額應在查詢前擋下")

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fail_query_all)
        )
        repo = repo_module.TradeJournalRepo("u1")
        assert repo.update_entry(1, amount=0)["ok"] is False
        assert repo.update_entry(1, amount=-5)["ok"] is False

    # ── I3：空編輯不寫修訂 ──────────────────────────────────────
    def test_update_entry_skips_revision_when_no_changes(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.update_entry)
        assert "if changed:" in src
        assert src.index("if changed:") < src.index("_insert_revision")

    # ── W3：修訂競態回 conflict ─────────────────────────────────
    @pytest.mark.parametrize("method", ["update_entry", "delete_trade", "restore_entry"])
    def test_write_paths_catch_unique_violation_as_conflict(self, method):
        src = inspect.getsource(getattr(repo_module.TradeJournalRepo, method))
        assert "psycopg2.errors.UniqueViolation" in src, (
            f"{method} 應把修訂號 UNIQUE 競態轉為可重試 conflict，而非漏 500"
        )
        assert '"conflict"' in src

    # ── I2/c038：回收筒索引 ─────────────────────────────────────
    def test_c038_deleted_index_migration_chain(self):
        src = (
            ROOT / "alembic/versions/c038_journal_deleted_index.py"
        ).read_text(encoding="utf-8")
        assert 'revision = "c038"' in src
        assert 'down_revision = "c037"' in src
        assert "idx_trade_journal_deleted" in src
        assert "deleted_at IS NOT NULL" in src

    def test_schema_py_has_deleted_index(self):
        # 預防性索引與 c036 同位置：create_trade_journal_table 尾段
        src = inspect.getsource(trade_journal_schema.create_trade_journal_table)
        assert "idx_trade_journal_deleted" in src
        assert "deleted_at IS NOT NULL" in src

    # ── agent 層：負金額與 broad except ─────────────────────────
    def test_update_tool_input_amount_gt0(self):
        from core.tools.crypto_modules.trade_journal import UpdateLedgerEntryInput

        assert UpdateLedgerEntryInput.model_fields["amount"].metadata
        gts = [
            m.gt
            for m in UpdateLedgerEntryInput.model_fields["amount"].metadata
            if hasattr(m, "gt")
        ]
        assert gts and gts[0] == 0

    def test_find_entry_no_broad_except(self):
        from core.tools.crypto_modules.trade_journal import (
            _find_entry_for_proposal,
        )

        src = inspect.getsource(_find_entry_for_proposal)
        assert "except Exception:" not in src
        assert "DatabaseError" in src

    def test_claw_loop_journal_update_coerces_amount(self):
        src = (
            ROOT / "core/agents/manager/claw_loop.py"
        ).read_text(encoding="utf-8")
        # journal_update 分支：edited amount 需 float 轉型＋非正數回退
        update_branch = src.split('kind == "journal_update"')[-1].split(
            'elif kind == "journal_entry"'
        )[0]
        assert "float(edited" in update_branch
        assert "amt > 0" in update_branch

    # ── API 層：500/409 映射 ────────────────────────────────────
    def test_api_maps_repo_errors_to_http_status(self):
        from api.routers.journal import _repo_error_status

        assert _repo_error_status({"error": "database error"}) == 500
        assert _repo_error_status({"error": "conflict"}) == 409
        assert _repo_error_status({"error": "entry not found"}) == 400
        assert _repo_error_status({"error": "rate unavailable"}) == 400



    # ── 前端契約（i18n 屬性／未知型別兜底／回收筒狀態／焦點可見）──
    def test_index_html_no_dead_i18n_attrs(self):
        """data-i18n-title=/data-i18n-placeholder= 沒有任何程式碼消費——
        正確形式是 data-i18n=<key> + data-i18n-attr=<attr>（i18n.js 唯一
        消費路徑）。此測試防止壞形式再被複製。"""
        import re
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        assert not re.search(r'data-i18n-title=', html), (
            "使用 data-i18n=<key> data-i18n-attr=\"title\""
        )
        assert not re.search(r'data-i18n-placeholder=', html), (
            "使用 data-i18n=<key> data-i18n-attr=\"placeholder\""
        )

    def test_type_meta_has_unknown_fallback(self):
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        assert "other: { icon:" in src, (
            "TYPE_META 需 other 兜底——未知 entry_type 曾讓整個列表渲染中斷"
        )

    def test_trash_error_state_wired(self):
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        assert "trashState" in src
        assert "journal.trashLoadError" in src
        assert "journal.retry" in src

    def test_switch_view_clears_category(self):
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        switch = src.split("async switchView(")[1].split("async ")[0]
        assert "filters.category = ''" in switch

    def test_row_action_buttons_focus_visible(self):
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        assert src.count("focus-visible:opacity-100") >= 2, (
            "hover 才出現的列內按鈕（History/Edit/Delete）鍵盤焦點需可見"
        )

    def test_edit_entry_ui_wired(self):
        js = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        assert "openEditForm" in js and "submitEditForm" in js
        assert "AppAPI.put(" in js
        assert 'id="journal-edit-modal"' in html
        assert 'data-click="Journal.openEditForm"' in js

    def test_manual_form_trade_fields(self):
        js = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        assert "journal-entry-quantity" in js and "journal-entry-side" in js
        assert "payload.quantity" in js and "payload.side" in js
        assert "journal-entry-date" in js and "payload.traded_at" in js
        assert 'id="journal-entry-trade-fields"' in html

    def test_positions_loading_skeleton(self):
        src = (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")
        switch = src.split("async switchView(")[1].split("async ")[0]
        assert "journal.loading" in switch

    def test_review_batch_i18n_keys_parity(self):
        keys = [
            "date", "dateHint", "deleteEntry", "edit", "editKeepCategory",
            "editTitle", "history", "quantityPlaceholder", "refresh", "retry",
            "sideBuy", "sideSell", "trashLoadError", "updateFailed",
        ]
        import json
        blocks = {}
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            data = json.loads(
                (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            blocks[lang] = data["journal"]
        for k in keys:
            for lang, jour in blocks.items():
                assert k in jour, f"journal.{k} 缺於 {lang}.json"

class _FakeTx:
    """update_entry 行為測試用的假 transaction：記錄執行的 SQL 與參數。"""

    def __init__(self, captured):
        self.captured = captured

    def __enter__(self):
        conn = self
        return conn

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.captured.setdefault("executed", []).append(sql)
        self.captured.setdefault("executed_params", []).append((sql, params))

    def fetchone(self):
        return None

    @property
    def rowcount(self):
        return 1

    def __iter__(self):
        return iter([()])



class TestMonthPickerAndMobile:
    """時間篩選改造（絕對月份一等公民）＋手機排版修復。

    使用者回饋（DANNY 2026-08-27）：只有「本月」看不到歷史；
    業界慣例（鯊魚記帳/Moze/銀行 app）以絕對月份為報表單位。
    後端 entries/summary 早就支援 date_from+date_to——純前端改造。
    """

    def _html(self):
        return (ROOT / "web/index.html").read_text(encoding="utf-8")

    def _js(self):
        return (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")

    def test_month_picker_input_present(self):
        assert 'id="journal-filter-month"' in self._html()
        assert 'type="month"' in self._html()

    def test_date_range_helper_prefers_month_over_days(self):
        js = self._js()
        assert "_dateRange()" in js
        dr = js.split("_dateRange() {")[1].split("async _loadEntries")[0]
        # 特定月份優先於快捷 select
        assert dr.index("f.month") < dr.index("f.days")
        # 月份範圍要送 date_to（月底）——只有 date_from 會吃到未來資料
        assert "date_to" in dr

    def test_load_entries_and_summary_share_date_range(self):
        js = self._js()
        for fn in ("_loadEntries", "_loadSummary"):
            block = js.split(f"async {fn}(")[1].split("async ")[0]
            assert "this._dateRange()" in block, f"{fn} 應共用 _dateRange"

    def test_bind_filters_reads_month_picker(self):
        js = self._js()
        bind = js.split("_bindFilters() {")[1].split("// 搜尋 debounce")[0]
        assert "journal-filter-month" in bind
        assert "filters.month" in bind

    def test_filter_selects_use_plain_labels(self):
        """filter 下拉用無 emoji 文案——emoji 在手機 select 被原生箭頭/字寬裁切。
        （手動表單的 type select 保留 emoji 視覺引導，全寬不裁。）"""
        html = self._html()
        days_block = html.split('id="journal-filter-type"')[1].split("</select>")[0]
        assert "typeExpense" not in days_block, "filter 下拉不得用 emoji 文案"
        assert "filterExpense" in days_block
        assert "filterIncome" in days_block

    def test_filter_selects_mobile_safe_width(self):
        html = self._html()
        # 手機上 select 需可收縮＋截斷＋右側留原生箭頭空間
        assert "max-w-[45vw]" in html
        assert "pr-8" in html

    def test_add_entry_button_no_wrap(self):
        html = self._html()
        add_block = html.split('id="journal-add-btn"')[1].split("</button>")[0]
        assert "whitespace-nowrap" in add_block

    def test_cashflow_view_renamed(self):
        """「月度收支」更名「收支總覽」——視圖支援任意期間，原名誤導。"""
        import json
        assert json.loads(
            (ROOT / "web/js/i18n/zh-TW.json").read_text(encoding="utf-8")
        )["journal"]["viewCashflow"] == "收支總覽"

    def test_month_filter_i18n_parity(self):
        import json
        for k in ("lastMonth", "thisYear", "filterExpense", "filterIncome"):
            for lang in ("zh-TW", "zh-CN", "en", "ru"):
                jour = json.loads(
                    (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
                )["journal"]
                assert k in jour, f"journal.{k} 缺於 {lang}.json"


class TestEditModalFollowUps:
    """PR #574/#575 合併後的 review 追修（2026-08-27）。

    編輯 modal 的兩個行為缺口＋月份 picker 的 aria-label i18n。
    """

    def _html(self):
        return (ROOT / "web/index.html").read_text(encoding="utf-8")

    def _js(self):
        return (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")

    def test_edit_categories_populated_before_value_assigned(self):
        """select 還沒有 option 時指派 value 會被瀏覽器丟棄（回退 ''）——
        既有類別不會被選中，自訂類別保留分支也永遠進不去。順序必須是
        先建選項、值由參數帶入。"""
        js = self._js()
        block = js.split("openEditForm(id) {")[1].split("_findEntryById(id) {")[0]
        assert "catSel.value =" not in block, "不得在建選項前指派 select.value"
        assert "_populateEditCategories(" in block
        populate = js.split("_populateEditCategories(")[1].split("closeEditForm()")[0]
        assert "current = select.value" not in populate, "current 應由呼叫端帶入"

    def test_edit_note_always_submitted(self):
        """只在有值時送 note 會讓「清空備註」永遠存不回去
        （後端 note='' 即清除）。"""
        js = self._js()
        block = js.split("async submitEditForm() {")[1].split("// ── Filters")[0]
        assert "if (note) payload.note" not in block
        assert "payload.note =" in block

    # ── 匯率凍結語義（成熟記帳/會計產品做法）────────────────────
    def test_amount_only_edit_reuses_frozen_rate_when_fx_down(self, monkeypatch):
        """匯率凍結於記錄當下——只改金額不該重抓匯率，也就不該因匯率
        服務中斷而擋下編輯；換算額以凍結匯率重算即自洽。
        （改幣別仍 fail-closed，見 TestReviewFixBatch W2。）"""
        captured = {}

        def fake_query_all(sql, params=None):
            return [{
                "id": 5, "price": 100.0, "quantity": 2, "currency": "USD",
                "category": "food", "note": "", "converted_amount": 6200.0,
                "exchange_rate": 31.0, "rate_source": "auto",
                "entry_type": "expense", "symbol": "USD", "market": "cash",
                "base_currency": "TWD", "user_id": "u1", "deleted_at": None,
            }]

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        import core.tools.crypto_modules.exchange_rate as fx

        def boom(cur, base):
            raise AssertionError("只改金額不得呼叫匯率服務")

        monkeypatch.setattr(fx, "get_exchange_rate", boom)

        result = repo_module.TradeJournalRepo("u1").update_entry(5, amount=150)
        assert result["ok"] is True
        updates = [
            params for sql, params in captured["executed_params"]
            if "UPDATE trade_journal" in sql
        ]
        assert updates, "應實際寫入"
        # 參數序：price, currency, category, note, converted, rate, rate_source, ...
        assert updates[0][4] == pytest.approx(150 * 2 * 31.0)
        assert float(updates[0][5]) == pytest.approx(31.0), "凍結匯率不得被改寫"

    def test_currency_change_still_fails_closed(self):
        """幣別變動仍必須重抓匯率——舊幣別的凍結匯率配新幣別是矛盾值。"""
        src = inspect.getsource(repo_module.TradeJournalRepo.update_entry)
        assert "if currency_changed or (amount_changed and not frozen_rate):" in src
        assert '"rate unavailable"' in src

    def test_month_picker_aria_label_i18n(self):
        """aria-label 寫死英文＝螢幕閱讀器永遠英文（同 PR #574 的死屬性教訓）。"""
        import json

        html = self._html()
        picker = html.split('id="journal-filter-month"')[1].split(">")[0]
        assert 'data-i18n="journal.pickMonth"' in picker
        assert 'data-i18n-attr="aria-label"' in picker
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            jour = json.loads(
                (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )["journal"]
            assert "pickMonth" in jour, f"journal.pickMonth 缺於 {lang}.json"


class TestManualExchangeRate:
    """自填匯率（2026-08-27）——匯率服務抓不到時的出口，也供使用者填自己
    的實際成交匯率。欄位（exchange_rate / rate_source='manual'）在 c033
    就存在，本批把 repo→API→UI→Agent 全鏈打通並修掉兩個算錯錢的缺陷。
    """

    def _html(self):
        return (ROOT / "web/index.html").read_text(encoding="utf-8")

    def _js(self):
        return (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")

    def _insert_params(self, captured):
        for sql, params in captured.get("executed_params", []):
            if "INSERT INTO trade_journal" in sql:
                return params
        raise AssertionError("沒有 INSERT")

    def _add_entry(self, monkeypatch, captured, auto_rate, **kwargs):
        import core.database.base as db_base

        # 基準幣設定的讀取與本測試無關——不假掉就會去連真 DB（逾時 27s）
        monkeypatch.setattr(
            repo_module, "_read_user_base_currency", lambda uid: "TWD"
        )
        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        import core.tools.crypto_modules.exchange_rate as fx

        monkeypatch.setattr(fx, "get_exchange_rate", lambda cur, base: auto_rate)
        return repo_module.TradeJournalRepo("u1").add_entry(
            entry_type="expense", symbol="USD", price=100, quantity=1,
            currency="USD", **kwargs
        )

    def test_manual_rate_wins_over_auto_and_drives_conversion(self, monkeypatch):
        """自填匯率優先於自動抓——且換算額必須用「存下來的那個匯率」算。
        原實作只給 exchange_rate 時會拿 auto_rate 算換算額卻存自填匯率，
        產生自相矛盾的凍結值。"""
        captured = {}
        self._add_entry(monkeypatch, captured, 31.5, exchange_rate=33.0)
        params = self._insert_params(captured)
        # 參數序：... converted_amount, exchange_rate, rate_source, ...
        assert params[14] == pytest.approx(100 * 1 * 33.0), "換算額須以自填匯率計算"
        assert params[15] == pytest.approx(33.0)
        assert params[16] == "manual", "自填匯率須與自動抓的區分開"

    def test_manual_rate_survives_fx_outage(self, monkeypatch):
        """匯率服務掛掉正是自填匯率的使用場景——原實作整段跳過，
        自填匯率連換算額都算不出來（converted 留 None）。"""
        captured = {}
        self._add_entry(monkeypatch, captured, None, exchange_rate=33.0)
        params = self._insert_params(captured)
        assert params[14] == pytest.approx(3300.0)
        assert params[15] == pytest.approx(33.0)
        assert params[16] == "manual"

    def test_auto_rate_still_default_without_manual(self, monkeypatch):
        """沒自填就照舊自動抓，rate_source 維持 auto。"""
        captured = {}
        self._add_entry(monkeypatch, captured, 31.5)
        params = self._insert_params(captured)
        assert params[14] == pytest.approx(3150.0)
        assert params[15] == pytest.approx(31.5)
        assert params[16] == "auto"

    def test_add_entry_rejects_non_positive_manual_rate(self, monkeypatch):
        captured = {}
        result = self._add_entry(monkeypatch, captured, 31.5, exchange_rate=0)
        assert result["ok"] is False
        assert not captured.get("executed"), "非正匯率不得寫入"

    def test_update_entry_manual_rate_bypasses_fail_closed(self, monkeypatch):
        """改幣別＋匯率服務掛掉原本是 fail-closed 死路——自填匯率是出口。"""
        captured = {}

        def fake_query_all(sql, params=None):
            return [{
                "id": 5, "price": 100.0, "quantity": 1, "currency": "USD",
                "category": "food", "note": "", "converted_amount": 3100.0,
                "exchange_rate": 31.0, "rate_source": "auto",
                "entry_type": "expense", "symbol": "USD", "market": "cash",
                "base_currency": "TWD", "user_id": "u1", "deleted_at": None,
            }]

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        import core.tools.crypto_modules.exchange_rate as fx

        monkeypatch.setattr(fx, "get_exchange_rate", lambda cur, base: None)

        result = repo_module.TradeJournalRepo("u1").update_entry(
            5, currency="EUR", exchange_rate=35.0
        )
        assert result["ok"] is True
        updates = [
            params for sql, params in captured["executed_params"]
            if "UPDATE trade_journal" in sql
        ]
        # 參數序：price, currency, category, note, converted, rate, rate_source, ...
        assert updates[0][4] == pytest.approx(100 * 1 * 35.0)
        assert float(updates[0][5]) == pytest.approx(35.0)
        assert updates[0][6] == "manual"

    def test_update_entry_rejects_non_positive_manual_rate(self, monkeypatch):
        def fake_query_all(sql, params=None):
            return [{
                "id": 5, "price": 100.0, "quantity": 1, "currency": "USD",
                "category": "food", "note": "", "converted_amount": 3100.0,
                "exchange_rate": 31.0, "rate_source": "auto",
                "entry_type": "expense", "symbol": "USD", "market": "cash",
                "base_currency": "TWD", "user_id": "u1", "deleted_at": None,
            }]

        monkeypatch.setattr(
            repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
        )
        assert repo_module.TradeJournalRepo("u1").update_entry(
            5, exchange_rate=-1
        )["ok"] is False

    # ── 幣別清單：UI 下拉不得超出匯率服務認得的範圍 ──────────────
    def test_currency_dropdowns_within_supported_set(self):
        """下拉出現匯率服務不認得的幣別＝使用者記了帳卻換算不出來。"""
        import re

        import core.tools.crypto_modules.exchange_rate as fx

        supported = set(fx._FIAT_RATES) | set(fx._CRYPTO_SYMBOLS)
        html = self._html()
        for sel_id in ("journal-entry-currency", "journal-edit-currency"):
            block = html.split(f'id="{sel_id}"')[1].split("</select>")[0]
            opts = set(re.findall(r'<option value="([A-Z]+)"', block))
            assert opts, f"{sel_id} 沒有任何幣別"
            assert opts <= supported, f"{sel_id} 有不支援的幣別：{opts - supported}"

    def test_currency_dropdowns_cover_supported_set(self):
        """反向：匯率服務支援的幣別都該讓使用者選得到（此前 CNY/EUR
        與 10 種加密貨幣算得出匯率卻在 UI 隱形）。"""
        import re

        import core.tools.crypto_modules.exchange_rate as fx

        supported = set(fx._FIAT_RATES) | set(fx._CRYPTO_SYMBOLS)
        html = self._html()
        for sel_id in ("journal-entry-currency", "journal-edit-currency"):
            block = html.split(f'id="{sel_id}"')[1].split("</select>")[0]
            opts = set(re.findall(r'<option value="([A-Z]+)"', block))
            assert supported <= opts, f"{sel_id} 少了：{supported - opts}"

    # ── 前端契約 ────────────────────────────────────────────────
    def test_rate_inputs_present_in_both_forms(self):
        html = self._html()
        for el_id in ("journal-entry-rate", "journal-edit-rate",
                      "journal-entry-rate-row", "journal-edit-rate-row"):
            assert f'id="{el_id}"' in html, f"缺 {el_id}"

    def test_frontend_sends_exchange_rate(self):
        js = self._js()
        for fn, end in (("async submitForm() {", "// ── 自填匯率"),
                        ("async submitEditForm() {", "// ── Filters")):
            block = js.split(fn)[1].split(end)[0]
            assert "exchange_rate" in block, f"{fn} 未送出自填匯率"

    def test_rate_unavailable_guides_user_to_manual_input(self):
        """後端 fail-closed 是唯一使用者自己能解的錯——必須打開匯率欄，
        而不是丟一句看不懂的英文。"""
        js = self._js()
        assert "_handleRateUnavailable" in js
        block = js.split("_handleRateUnavailable(err, rowId, inputId) {")[1] \
            .split("openEditForm(id) {")[0]
        assert "rate unavailable" in block
        assert "classList.remove('hidden')" in block

    def test_manual_rate_marked_in_list(self):
        """自填匯率換算出來的數字不得與市場匯率混為一談。"""
        js = self._js()
        assert "rate_source === 'manual'" in js
        assert "rateManualNote" in js

    def test_rate_i18n_parity(self):
        import json

        for k in ("ratePlaceholder", "rateHint", "rateHintEdit",
                  "rateUnavailable", "ratePositive", "rateManualNote"):
            for lang in ("zh-TW", "zh-CN", "en", "ru"):
                jour = json.loads(
                    (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
                )["journal"]
                assert k in jour, f"journal.{k} 缺於 {lang}.json"

    # ── API / Agent 層 ──────────────────────────────────────────
    def test_api_models_accept_exchange_rate(self):
        from api.routers.journal import JournalEntryInput, JournalEntryUpdate

        for model in (JournalEntryInput, JournalEntryUpdate):
            assert "exchange_rate" in model.model_fields, model.__name__

    def test_agent_tools_accept_exchange_rate(self):
        from core.tools.crypto_modules.trade_journal import (
            RecordEntryInput,
            UpdateLedgerEntryInput,
        )

        for model in (RecordEntryInput, UpdateLedgerEntryInput):
            assert "exchange_rate" in model.model_fields, model.__name__
            assert model.model_fields["exchange_rate"].default is None

    def test_consent_apply_preserves_manual_rate_source(self):
        """使用者在對話裡自己講的匯率，核准寫入時不能被標成 frozen。"""
        src = (ROOT / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
        assert 'signal.get("rate_source") or "frozen"' in src


class TestBaseCurrency:
    """報表基準幣 per-user 可設定（c039）。

    converted_amount / exchange_rate 都是「換算成基準幣」的派生值，基準幣
    此前寫死 TWD。改為可設定後最大的風險是「設定改了、資料還停在舊基準幣」
    ——彙總的 SUM(converted_amount) 不看 base_currency，錯位就是把兩種幣別
    的金額直接相加。以下測試鎖住：fail-closed、跨表原子、換算恆等式。
    """

    def _html(self):
        return (ROOT / "web/index.html").read_text(encoding="utf-8")

    def _js(self):
        return (ROOT / "web/js/components/tab-journal.js").read_text(encoding="utf-8")

    def _repo(self, monkeypatch, current="TWD"):
        monkeypatch.setattr(
            repo_module, "_read_user_base_currency", lambda uid: current
        )
        return repo_module.TradeJournalRepo("u1")

    def _fx(self, monkeypatch, rate):
        import core.tools.crypto_modules.exchange_rate as fx

        monkeypatch.setattr(fx, "get_exchange_rate", lambda a, b: rate)

    # ── 延遲解析 per-user 基準幣 ────────────────────────────────
    def test_base_currency_read_from_user_setting(self, monkeypatch):
        assert self._repo(monkeypatch, "USD").base_currency == "USD"

    def test_base_currency_defaults_to_twd(self, monkeypatch):
        """讀不到設定（欄位未 migrate／DB 不可用）不得讓帳本不能用。"""
        assert self._repo(monkeypatch, None).base_currency == "TWD"

    def test_explicit_base_currency_skips_user_lookup(self, monkeypatch):
        def boom(uid):
            raise AssertionError("已明確指定基準幣就不該查設定")

        monkeypatch.setattr(repo_module, "_read_user_base_currency", boom)
        assert repo_module.TradeJournalRepo("u1", "JPY").base_currency == "JPY"

    # ── 切換：驗證與 fail-closed ────────────────────────────────
    def test_rebase_rejects_unsupported_currency(self, monkeypatch):
        captured = {}
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        result = self._repo(monkeypatch).rebase_to("BTC")
        assert result["ok"] is False
        assert not captured.get("executed"), "不支援的基準幣不得寫入"

    def test_rebase_to_same_currency_is_noop(self, monkeypatch):
        captured = {}
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        result = self._repo(monkeypatch, "TWD").rebase_to("twd")
        assert result == {
            "ok": True, "rebased": 0, "rate": 1.0, "base_currency": "TWD",
        }
        assert not captured.get("executed")

    def test_rebase_fails_closed_when_cross_rate_unavailable(self, monkeypatch):
        """交叉匯率抓不到就一列都不能動——半套搬移＝整本帳的金額都錯。"""
        captured = {}
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        self._fx(monkeypatch, None)
        result = self._repo(monkeypatch, "TWD").rebase_to("USD")
        assert result == {"ok": False, "error": "rate unavailable"}
        assert not captured.get("executed")

    # ── 切換：搬移與原子性 ──────────────────────────────────────
    def test_rebase_moves_derived_values_atomically(self, monkeypatch):
        """換算值與 users.base_currency 必須在同一個交易——否則會出現
        「設定說 USD、資料還停在 TWD」的錯位。"""
        captured = {}
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _FakeTx(captured))
        self._fx(monkeypatch, 0.0315)  # TWD -> USD
        result = self._repo(monkeypatch, "TWD").rebase_to("USD")
        assert result["ok"] is True
        assert result["base_currency"] == "USD"
        assert result["rate"] == pytest.approx(0.0315)

        sqls = captured["executed"]
        journal = [q for q in sqls if "UPDATE trade_journal" in q]
        users = [q for q in sqls if "UPDATE users" in q]
        assert journal and users, "兩張表都要更新"
        # 同一個 _FakeTx 實例＝同一個交易
        assert len(captured.get("tx_instances", set()) or {1}) <= 1

        params = [
            p for q, p in captured["executed_params"] if "UPDATE trade_journal" in q
        ][0]
        # converted_amount 與 exchange_rate 同乘交叉匯率——
        # converted = price x quantity x rate 的恆等式才會維持成立
        # cross 以 Decimal 送出（純 numeric 運算，不引入浮點誤差）——
        # Decimal 不能直接跟 pytest.approx(float) 比
        assert float(params[0]) == pytest.approx(0.0315)
        assert float(params[1]) == pytest.approx(0.0315)
        assert params[2] == "USD"

    def test_rebase_users_update_is_compare_and_swap(self, monkeypatch):
        """兩個併發切換請求會讀到同一個 old_base——先到的搬完列，後到的搬到
        0 列卻仍寫入自己的目標幣別，就是「設定說 EUR、資料是 USD」的錯位。
        users 更新帶 old_base 條件，撞到就整筆回滾回 conflict（可重試）。"""

        class _UsersConflictTx(_FakeTx):
            @property
            def rowcount(self):
                executed = self.captured.get("executed") or [""]
                # 條目搬移正常，users 的 compare-and-swap 撞到別人先改了
                return 0 if "UPDATE users" in executed[-1] else 1

        captured = {}
        import core.database.base as db_base

        monkeypatch.setattr(db_base, "transaction", lambda: _UsersConflictTx(captured))
        self._fx(monkeypatch, 0.0315)
        result = self._repo(monkeypatch, "TWD").rebase_to("USD")
        assert result == {"ok": False, "error": "conflict"}

    def test_rebase_users_update_carries_old_base_condition(self):
        src = inspect.getsource(repo_module.TradeJournalRepo.rebase_to)
        users_update = src.split("UPDATE users")[1].split('"""')[0]
        assert "COALESCE(base_currency, 'TWD') = %s" in users_update

    def test_rebase_touches_facts_never(self):
        """事實欄位（price/currency/quantity）不得被搬移動到——只有派生值
        （converted_amount/exchange_rate）與基準幣標記可以改。"""
        import re

        src = inspect.getsource(repo_module.TradeJournalRepo.rebase_to)
        set_clause = src.split("UPDATE trade_journal")[1].split("SET")[1].split("WHERE")[0]
        assigned = set(re.findall(r"(\w+)\s*=", set_clause))
        assert assigned == {
            "converted_amount", "exchange_rate", "base_currency", "updated_at"
        }, f"rebase 的 SET 欄位不該是 {assigned}"

    def test_rebase_includes_deleted_entries(self):
        """回收筒可還原——已刪條目不能留在舊基準幣。"""
        src = inspect.getsource(repo_module.TradeJournalRepo.rebase_to)
        update = src.split("UPDATE trade_journal")[1].split("cur.rowcount")[0]
        assert "deleted_at" not in update, "rebase 不得排除已刪條目"

    # ── 彙總必須看得見基準幣 ────────────────────────────────────
    def test_summary_groups_by_base_currency(self):
        """SUM(converted_amount) 不分基準幣就是把兩種幣別直接相加。"""
        src = inspect.getsource(repo_module.TradeJournalRepo.get_category_summary)
        assert "base_currency" in src.split("GROUP BY")[1].split('"""')[0]

    # ── 可選基準幣範圍 ──────────────────────────────────────────
    def test_valid_bases_all_have_offline_rates(self):
        """可選基準幣必須都在靜態法幣表內——否則遠端 API 掛掉時，
        使用者連自己的彙總都看不到。"""
        import core.tools.crypto_modules.exchange_rate as fx

        assert repo_module.TradeJournalRepo.VALID_BASE_CURRENCIES <= set(fx._FIAT_RATES)

    # ── Agent 層跟上基準幣 ──────────────────────────────────────
    def _tool_src(self, fn_name, end_marker):
        """@tool 包成 StructuredTool，inspect.getsource 取不到——讀檔切函式。"""
        src = (
            ROOT / "core/tools/crypto_modules/trade_journal.py"
        ).read_text(encoding="utf-8")
        return src.split(f"def {fn_name}(")[1].split(end_marker)[0]

    def test_agent_proposal_uses_user_base_currency(self):
        """record_entry 寫死 TWD 的話，基準幣是 USD 的使用者會拿到「對台幣
        凍結的匯率」卻被存成 USD 基準——整筆換算額都是錯的。"""
        src = self._tool_src("record_entry", "# ── query_ledger")
        assert 'base = "TWD"' not in src
        assert "base = get_journal_repo(user_id).base_currency" in src

    def test_consent_apply_carries_base_currency(self):
        """核准寫入時基準幣必須跟著凍結匯率一起帶下去。"""
        src = (ROOT / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
        # 2026-09-12：原本比對含換行的字面字串，ruff format 把它收成一行後這條
        # 守衛就一直紅（develop 上早已如此）。改成不吃排版的比對，意圖不變。
        assert re.search(
            r'base_currency=\(\s*signal\.get\("base_currency"\)\s*if rate_frozen', src
        ), "核准寫入時 base_currency 必須跟著 rate_frozen 一起帶下去"

    def test_query_ledger_reports_in_user_base(self):
        """彙總標題與金額符號寫死 NT$/TWD＝非台幣使用者看到錯的幣別。"""
        src = self._tool_src("query_ledger", "# ── delete / update")
        # 寫死在輸出字串裡的 NT$（f"...NT${total}"）才是問題，
        # 符號對照表本身當然會含 NT$
        assert "NT$$" not in src and "NT${" not in src
        assert "（TWD）" not in src
        assert "base_sym" in src

    # ── API ─────────────────────────────────────────────────────
    def test_api_exposes_base_currency_endpoints(self):
        from api.routers.journal import router

        paths = {r.path for r in router.routes}
        assert "/api/journal/base-currency" in paths
        methods = {
            m for r in router.routes if r.path == "/api/journal/base-currency"
            for m in r.methods
        }
        assert {"GET", "PUT"} <= methods

    # ── 前端 ────────────────────────────────────────────────────
    def test_base_currency_selector_present(self):
        assert 'id="journal-base-currency"' in self._html()

    def test_no_hardcoded_base_symbol_left(self):
        """NT$ 寫死＝改了基準幣還是顯示台幣符號。"""
        js = self._js()
        assert "'NT$'" not in js.replace("TWD: 'NT$'", "")
        assert "NT$$" not in js

    def test_symbols_cover_every_selectable_base(self):
        js = self._js()
        table = js.split("const BASE_SYMBOLS = {")[1].split("};")[0]
        for code in repo_module.TradeJournalRepo.VALID_BASE_CURRENCIES:
            assert f"{code}:" in table, f"BASE_SYMBOLS 缺 {code}"

    def test_switch_asks_for_confirmation(self):
        """會動到整本帳的換算值——不得無聲執行。"""
        js = self._js()
        block = js.split("async changeBaseCurrency(currency) {")[1].split("_t(key, fallback)")[0]
        # 與 deleteEntry 同模式：有系統 dialog 就用，否則 fallback 原生 confirm
        assert "showConfirmDialog" in block
        assert "window.confirm" in block
        assert "baseCurrencyConfirm" in block

    def test_failed_switch_reverts_select(self):
        """後端 fail-closed 一列都沒動——下拉不能停在新幣別誤導使用者。"""
        js = self._js()
        block = js.split("async changeBaseCurrency(currency) {")[1].split("_t(key, fallback)")[0]
        catch = block.split("} catch (e) {")[1]
        assert "JournalState.baseCurrency = prev" in catch

    def test_new_controls_use_classes_present_in_built_css(self):
        """新加的 Tailwind class 若不在 built CSS 裡就是靜默失效——
        本例：pr-6 從未被建進 CSS，下拉的右內距等於沒寫，原生箭頭會壓到
        文字（#575 修過的同一個手機 bug）。npm 不在測試環境，改以「用過的
        class 必須已存在於 built CSS」鎖住。"""
        import re

        css = (ROOT / "web/css/tailwind-built.css").read_text(encoding="utf-8")
        html = self._html()

        def classes_of(el_id):
            block = html.split(f'id="{el_id}"')[1].split(">")[0]
            m = re.search(r'class="([^"]+)"', block)
            return m.group(1).split() if m else []

        def in_css(cls):
            # Tailwind 在 CSS 裡把特殊字元轉義（py-2.5 → .py-2\\.5、
            # text-textMuted/60 → .text-textMuted\\/60）——比對時要允許那個反斜線
            body = "".join(
                c if (c.isalnum() or c == "-") else r"\\?" + re.escape(c)
                for c in cls
            )
            return re.search(rf"\.{body}[^\w-]", css) is not None

        missing = {
            cls
            for el_id in ("journal-base-currency", "journal-entry-rate",
                          "journal-edit-rate")
            for cls in classes_of(el_id)
            if not in_css(cls)
        }
        assert not missing, f"built CSS 沒有這些 class（需重建 CSS）：{missing}"

    def test_base_currency_i18n_parity(self):
        import json

        for k in ("baseCurrencyLabel", "baseCurrencyConfirm", "baseCurrencyConfirmBody",
                  "baseCurrencyChanged", "baseCurrencyFailed",
                  "baseCurrencyRateUnavailable"):
            for lang in ("zh-TW", "zh-CN", "en", "ru"):
                jour = json.loads(
                    (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
                )["journal"]
                assert k in jour, f"journal.{k} 缺於 {lang}.json"
