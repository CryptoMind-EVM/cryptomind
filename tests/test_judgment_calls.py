"""判斷評分 v2：明確喊單（設計 §7）。純函式、工具提案 marker、consent 卡、API 契約、
schema 守衛、前端接線。不打 DB（DB 函式 monkeypatch）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.scorecard import calls, rules

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[1]


class TestNormalize:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("bullish", "buy"),
            ("Bearish", "sell"),
            ("long", "buy"),
            ("short", "sell"),
            ("buy", "buy"),
        ],
    )
    def test_sides(self, raw, expected):
        assert calls.normalize_side(raw) == expected

    def test_bad_side(self):
        with pytest.raises(calls.CallError):
            calls.normalize_side("maybe")

    @pytest.mark.parametrize(
        "raw, expected", [(None, 30), ("", 30), (7, 7), ("90", 90), (365, 365)]
    )
    def test_horizons(self, raw, expected):
        assert calls.normalize_horizon(raw) == expected

    @pytest.mark.parametrize("raw", [6, 366, "x", 0])
    def test_bad_horizons(self, raw):
        with pytest.raises(calls.CallError):
            calls.normalize_horizon(raw)

    def test_symbol_market_inference(self):
        assert calls.normalize_symbol_market("btc") == ("BTC", "crypto")
        assert calls.normalize_symbol_market("2330.TW") == ("2330.TW", "tw_stock")
        assert calls.normalize_symbol_market("aapl", "us_stock") == ("AAPL", "us_stock")

    def test_stablecoin_and_unscorable_market_rejected(self):
        with pytest.raises(calls.CallError, match="stablecoin"):
            calls.normalize_symbol_market("USDT")
        with pytest.raises(calls.CallError, match="not scorable"):
            calls.normalize_symbol_market("USDTWD", "forex")
        with pytest.raises(calls.CallError):
            calls.normalize_symbol_market("")


class TestPendingRow:
    def test_row_shape_and_benchmark(self):
        call = {
            "id": 7,
            "symbol": "eth",
            "market": "crypto",
            "side": "buy",
            "horizon_days": 45,
            "entry_price": 3000.0,
            "entry_date": date(2026, 9, 13),
        }
        row = calls.pending_row_for_call(call)
        assert row["kind"] == "call" and row["call_id"] == 7 and row["entry_id"] == 0
        assert row["exit_date"] == date(2026, 10, 28)
        assert (
            row["bench_symbol"] == "BTC" and row["verification"] == rules.SELF_REPORTED
        )
        # 標的就是基準 → 不扣基準
        assert (
            calls.pending_row_for_call({**call, "symbol": "BTC"})["bench_symbol"]
            is None
        )


class TestLatestClose:
    def test_walks_back_over_weekend(self, monkeypatch):
        seen = []

        def fake(symbol, market, on):
            seen.append(on)
            return 100.0 if on == date(2026, 9, 11) else None  # 週五

        monkeypatch.setattr(calls.prices, "close_on", fake)
        assert calls.latest_close("AAPL", "us_stock", date(2026, 9, 13)) == (
            100.0,
            date(2026, 9, 11),
        )
        assert seen[:3] == [date(2026, 9, 13), date(2026, 9, 12), date(2026, 9, 11)]

    def test_none_when_nothing(self, monkeypatch):
        monkeypatch.setattr(calls.prices, "close_on", lambda *a: None)
        assert calls.latest_close("X", "crypto", date(2026, 9, 13)) is None


class TestCreateAndCancel:
    def test_create_writes_call_then_pending(self, monkeypatch):
        inserted = {}
        monkeypatch.setattr(calls.prices, "close_on", lambda s, m, on: 61000.0)

        class _Cur:
            description = [
                type("D", (), {"name": n})()
                for n in (
                    "id",
                    "user_id",
                    "symbol",
                    "market",
                    "side",
                    "horizon_days",
                    "target_price",
                    "entry_price",
                    "entry_date",
                    "note",
                    "source",
                    "status",
                    "created_at",
                )
            ]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params):
                inserted["params"] = params

            def fetchone(self):
                return (
                    5,
                    "u1",
                    "BTC",
                    "crypto",
                    "buy",
                    30,
                    70000.0,
                    61000.0,
                    date(2026, 9, 13),
                    "",
                    "chat",
                    "open",
                    datetime(2026, 9, 13, tzinfo=timezone.utc),
                )

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return _Cur()

        monkeypatch.setattr(calls, "transaction", lambda: _Conn())
        pend = {}
        monkeypatch.setattr(
            calls.store,
            "insert_pending",
            lambda uid, rows: pend.update(uid=uid, rows=rows) or 1,
        )
        out = calls.create_call(
            "u1",
            symbol="btc",
            side="bullish",
            horizon_days=30,
            target_price=70000,
            today=date(2026, 9, 13),
        )
        assert inserted["params"][:6] == ("u1", "BTC", "crypto", "buy", 30, 70000.0)
        assert (
            out["id"] == 5
            and out["entry_price"] == 61000.0
            and out["entry_date"] == "2026-09-13"
        )
        assert (
            pend["uid"] == "u1"
            and pend["rows"][0]["call_id"] == 5
            and pend["rows"][0]["exit_date"] == date(2026, 10, 13)
        )

    def test_create_without_price_raises(self, monkeypatch):
        monkeypatch.setattr(calls.prices, "close_on", lambda *a: None)
        with pytest.raises(calls.CallError, match="no recent close"):
            calls.create_call("u1", symbol="BTC", side="bullish")

    def test_cancel_window_and_scored_guard(self, monkeypatch):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        rows = {
            "call": {"id": 1, "status": "open", "created_at": now - timedelta(hours=25)}
        }
        monkeypatch.setattr(
            calls.DatabaseBase,
            "query_one",
            lambda sql, params: rows["call"] if "judgment_calls" in sql else None,
        )
        assert calls.cancel_call("u1", 1, now=now) == {
            "ok": False,
            "error": "cancel window closed",
        }
        rows["call"] = {"id": 1, "status": "scored", "created_at": now}
        assert calls.cancel_call("u1", 1, now=now)["error"] == "call is scored"
        rows["call"] = None
        assert calls.cancel_call("u1", 1, now=now)["error"] == "not found"


class TestToolProposal:
    def test_marker_shape(self, monkeypatch):
        from core.tools import scorecard_tool as st

        monkeypatch.setattr("core.tools.key_resolver.get_current_user_id", lambda: "u1")
        out = json.loads(
            st.record_call.invoke(
                {
                    "symbol": "eth",
                    "side": "bearish",
                    "horizon_days": 90,
                    "target_price": 2500,
                    "note": "ETF outflows",
                }
            )
        )
        assert out["__needs_consent__"] is True and out["kind"] == "judgment_call"
        assert (
            out["symbol"] == "ETH"
            and out["market"] == "crypto"
            and out["side"] == "sell"
        )
        assert (
            out["direction"] == "bearish"
            and out["horizon_days"] == 90
            and out["target_price"] == 2500.0
        )
        assert len(out["proposal_id"]) == 12
        again = json.loads(
            st.record_call.invoke(
                {
                    "symbol": "eth",
                    "side": "bearish",
                    "horizon_days": 90,
                    "target_price": 2500,
                    "note": "ETF outflows",
                }
            )
        )
        assert again["proposal_id"] == out["proposal_id"], "同內容不同 ts 要同 identity"

    def test_tool_rejects_bad_input_without_marker(self, monkeypatch):
        from core.tools import scorecard_tool as st

        monkeypatch.setattr("core.tools.key_resolver.get_current_user_id", lambda: "u1")
        out = json.loads(st.record_call.invoke({"symbol": "USDT", "side": "bullish"}))
        assert "error" in out and "__needs_consent__" not in out

    def test_tool_requires_login(self, monkeypatch):
        from core.tools import scorecard_tool as st

        monkeypatch.setattr("core.tools.key_resolver.get_current_user_id", lambda: None)
        assert "error" in json.loads(
            st.record_call.invoke({"symbol": "BTC", "side": "bullish"})
        )


class TestConsentWiring:
    def test_multi_card_renders_call(self):
        from core.agents.manager.consent_gate import build_multi_consent_cards

        cards = build_multi_consent_cards(
            [
                {
                    "kind": "judgment_call",
                    "symbol": "BTC",
                    "side": "buy",
                    "horizon_days": 30,
                    "target_price": 70000,
                    "note": "halving",
                }
            ],
            "zh-TW",
        )
        c = cards[0]
        assert (
            c["kind"] == "judgment_call"
            and c["icon"] == "🎯"
            and c["type"] == "journal_consent"
        )
        assert (
            "BTC" in c["lines"][0]
            and "看多" in c["lines"][0]
            and "30 天" in c["lines"][0]
        )
        assert any("70000" in ln for ln in c["lines"]) and any(
            "24" in ln for ln in c["lines"]
        )
        assert c["danger"] is False
        en = build_multi_consent_cards(
            [
                {
                    "kind": "judgment_call",
                    "symbol": "BTC",
                    "side": "sell",
                    "horizon_days": 7,
                }
            ],
            "en",
        )[0]
        assert "bearish" in en["lines"][0] and "7 days" in en["lines"][0]

    def test_identity_uses_proposal_id(self):
        from core.agents.manager.claw_loop import _consent_identity

        assert (
            _consent_identity({"kind": "judgment_call", "proposal_id": "abc", "ts": 1})
            == "judgment_call:abc"
        )

    def test_apply_consent_write_creates_call(self, monkeypatch):
        from core.agents.manager import claw_loop

        got = {}

        def fake_create(user_id, **kw):
            got.update(kw, user_id=user_id)
            return {
                "id": 42,
                "symbol": kw["symbol"],
                "side": kw["side"],
                "horizon_days": kw["horizon_days"],
            }

        monkeypatch.setattr(calls, "create_call", fake_create)
        signal = {
            "kind": "judgment_call",
            "symbol": "BTC",
            "market": "crypto",
            "side": "buy",
            "horizon_days": 30,
            "target_price": None,
            "note": "x",
        }
        assert claw_loop._apply_consent_write("u1", signal, {"note": "edited"}) == 42
        assert (
            got["user_id"] == "u1"
            and got["note"] == "edited"
            and got["source"] == "chat"
        )

    def test_apply_consent_write_swallows_call_error(self, monkeypatch):
        from core.agents.manager import claw_loop

        def boom(*a, **k):
            raise calls.CallError("no recent close price")

        monkeypatch.setattr(calls, "create_call", boom)
        assert (
            claw_loop._apply_consent_write(
                "u1", {"kind": "judgment_call", "symbol": "BTC", "side": "buy"}, {}
            )
            is None
        )

    def test_not_in_single_card_kinds(self):
        from core.agents.manager.claw_loop import _SINGLE_CARD_KINDS

        assert "judgment_call" not in _SINGLE_CARD_KINDS


class TestApiAndWiring:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.deps import get_current_user
        from api.routers.scorecard import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
        return TestClient(app)

    def test_create_and_list_and_cancel(self, client, monkeypatch):
        from api.routers import scorecard as r

        monkeypatch.setattr(
            r.calls,
            "create_call",
            lambda uid, **kw: {
                "id": 1,
                "symbol": kw["symbol"].upper(),
                "side": "buy",
                "horizon_days": kw["horizon_days"],
                "entry_price": 1.0,
            },
        )
        res = client.post(
            "/api/journal/scorecard/calls",
            json={"symbol": "btc", "side": "bullish", "horizon_days": 30},
        )
        assert res.status_code == 201 and res.json()["call"]["symbol"] == "BTC"
        monkeypatch.setattr(r.calls, "list_calls", lambda uid, **kw: [{"id": 1}])
        assert client.get("/api/journal/scorecard/calls").json()["calls"] == [{"id": 1}]
        monkeypatch.setattr(
            r.calls,
            "cancel_call",
            lambda uid, cid: {"ok": False, "error": "cancel window closed"},
        )
        assert client.delete("/api/journal/scorecard/calls/1").status_code == 409
        monkeypatch.setattr(
            r.calls, "cancel_call", lambda uid, cid: {"ok": False, "error": "not found"}
        )
        assert client.delete("/api/journal/scorecard/calls/1").status_code == 404

    def test_create_validation(self, client, monkeypatch):
        from api.routers import scorecard as r

        assert (
            client.post(
                "/api/journal/scorecard/calls", json={"symbol": "BTC", "side": "maybe"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/journal/scorecard/calls",
                json={"symbol": "BTC", "side": "bullish", "horizon_days": 3},
            ).status_code
            == 422
        )

        def boom(uid, **kw):
            raise calls.CallError("no recent close price for X")

        monkeypatch.setattr(r.calls, "create_call", boom)
        res = client.post(
            "/api/journal/scorecard/calls", json={"symbol": "X", "side": "bullish"}
        )
        assert res.status_code == 400 and "no recent close" in res.json()["detail"]

    def test_tool_registered_everywhere(self):
        boot = (REPO / "core" / "agents" / "bootstrap.py").read_text(encoding="utf-8")
        assert 'name="record_call"' in boot
        seed = (REPO / "core" / "database" / "tools.py").read_text(encoding="utf-8")
        assert '"tool_id": "record_call"' in seed
        assert seed.count('"record_call"') >= 2, "seed 與 cryptomind 類別清單都要有"
        tr = (REPO / "core" / "agents" / "tool_name_translations.py").read_text(
            encoding="utf-8"
        )
        assert '"record_call": {' in tr

    def test_schema_and_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("judgment_calls", create_judgment_calls_table)' in schema
        mig = (REPO / "alembic" / "versions" / "c050_judgment_calls.py").read_text(
            encoding="utf-8"
        )
        assert 'revision = "c050"' in mig and 'down_revision = "c049"' in mig
        store_src = (REPO / "core" / "scorecard" / "store.py").read_text(
            encoding="utf-8"
        )
        assert 'int(r.get("call_id") or 0)' in store_src, (
            "insert_pending 不能再把 call_id 寫死 0"
        )

    def test_frontend_wiring(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        for i in (
            "journal-calls",
            "journal-call-symbol",
            "journal-call-side",
            "journal-call-horizon",
            "journal-calls-list",
        ):
            assert f'id="{i}"' in html
        assert 'data-click="Journal.submitCall"' in html
        js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(
            encoding="utf-8"
        )
        assert (
            "submitCall: () => JournalTab.submitCall()" in js
            and "cancelCall: (id) => JournalTab.cancelCall(id)" in js
        )
        assert "/api/journal/scorecard/calls" in js
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            d = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert (
                d["journal"]["calls"]["submit"]
                and d["journal"]["calls"]["cancelFailed"]
            )
