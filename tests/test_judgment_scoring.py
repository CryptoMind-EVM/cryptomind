"""判斷評分（2026-09-13；設計 docs/plans/2026-09-13-judgment-scoring-design.md）：
純函式規則、建檔／評分服務（store 假造）、cron 隔離、API 契約、工具、schema／UI／i18n 守衛。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.scorecard import rules, service, store

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


# ── 規則 ─────────────────────────────────────────────────────────────────────


class TestRules:
    def test_verification(self):
        assert (
            rules.verification_for(
                {
                    "traded_at": "2026-09-01",
                    "created_at": "2026-09-01T10:00:00",
                    "source": "onchain",
                    "tx_hash": "0x1",
                }
            )
            == "verified"
        )
        assert (
            rules.verification_for(
                {
                    "traded_at": "2026-09-01",
                    "created_at": "2026-09-02",
                    "source": "manual",
                }
            )
            == "self_reported"
        )
        # 補記：記錄比成交晚超過 3 天 → 不計分（就算是鏈上同步的也一樣）
        assert (
            rules.verification_for(
                {
                    "traded_at": "2026-08-01",
                    "created_at": "2026-09-01",
                    "source": "onchain",
                    "tx_hash": "0x1",
                }
            )
            == "backfilled"
        )
        assert (
            rules.verification_for(
                {
                    "traded_at": "2026-09-01",
                    "created_at": "2026-09-04",
                    "source": "manual",
                }
            )
            == "self_reported"
        )

    def test_scorable_trade_filter(self):
        base = {
            "entry_type": "trade",
            "market": "us_stock",
            "side": "buy",
            "price": 100,
            "quantity": 1,
            "traded_at": "2026-09-01",
        }
        assert rules.is_scorable_trade(base)
        assert not rules.is_scorable_trade({**base, "market": "forex"})
        assert not rules.is_scorable_trade({**base, "entry_type": "expense"})
        assert not rules.is_scorable_trade({**base, "price": 0})
        assert not rules.is_scorable_trade({**base, "side": "hold"})
        assert not rules.is_scorable_trade(
            {**base, "market": "crypto", "symbol": "USDC"}
        )  # 穩定幣不是判斷

    def test_pending_rows_two_horizons_with_benchmark(self):
        rows = rules.pending_rows_for(
            {
                "id": 7,
                "symbol": "nvda",
                "market": "us_stock",
                "side": "buy",
                "price": "100.5",
                "traded_at": "2026-09-01T13:00:00",
                "created_at": "2026-09-01T13:05:00",
                "source": "manual",
            }
        )
        assert [(r["horizon_days"], r["exit_date"].isoformat()) for r in rows] == [
            (30, "2026-10-01"),
            (90, "2026-11-30"),
        ]
        assert (
            rows[0]["symbol"] == "NVDA"
            and rows[0]["bench_symbol"] == "SPY"
            and rows[0]["verification"] == "self_reported"
        )
        assert (
            rules.pending_rows_for(
                {
                    "id": 1,
                    "symbol": "ETH",
                    "market": "crypto",
                    "side": "sell",
                    "price": 1,
                    "traded_at": "2026-09-01",
                    "created_at": "2026-09-01",
                }
            )[0]["bench_symbol"]
            == "BTC"
        )
        # 標的就是基準（BTC／0050）→ 不扣基準
        for sym, market in (("BTC", "crypto"), ("0050", "tw_stock")):
            row = rules.pending_rows_for(
                {
                    "id": 1,
                    "symbol": sym,
                    "market": market,
                    "side": "buy",
                    "price": 1,
                    "traded_at": "2026-09-01",
                    "created_at": "2026-09-01",
                }
            )[0]
            assert row["bench_symbol"] is None

    def test_score_long_short_and_benchmark(self):
        s = rules.score(
            side="buy", entry_price=100, exit_price=110, bench_entry=100, bench_exit=105
        )
        assert (s.raw_pct, s.bench_pct, s.excess_pct, s.hit) == (10.0, 5.0, 5.0, True)
        s = rules.score(
            side="sell",
            entry_price=100,
            exit_price=110,
            bench_entry=100,
            bench_exit=105,
        )
        assert (s.raw_pct, s.bench_pct, s.excess_pct, s.hit) == (
            -10.0,
            -5.0,
            -5.0,
            False,
        )
        # 牛市裡漲得比大盤少 → 命中失敗（這就是幣安式勝率跟這裡的差別）
        s = rules.score(
            side="buy", entry_price=100, exit_price=103, bench_entry=100, bench_exit=108
        )
        assert s.excess_pct == -5.0 and not s.hit
        s = rules.score(side="buy", entry_price=100, exit_price=103)
        assert s.bench_pct is None and s.excess_pct == 3.0 and s.hit

    def test_summarize_masks_small_samples_and_picks_headline(self):
        def row(
            i,
            verification="verified",
            horizon=90,
            excess=1.0,
            market="crypto",
            status="scored",
        ):
            return {
                "status": status,
                "verification": verification,
                "market": market,
                "horizon_days": horizon,
                "excess_pct": excess,
                "hit": excess > 0,
            }

        rows = [
            row(i, excess=(2.0 if i % 3 else -1.0)) for i in range(12)
        ]  # verified/crypto/90：12 筆
        rows += [
            row(i, verification="self_reported", excess=0.5) for i in range(4)
        ]  # 4 筆 → 遮罩
        rows += [
            row(i, verification="backfilled", excess=99.0) for i in range(20)
        ]  # 補記不計
        rows += [row(i, status="pending") for i in range(3)]
        out = rules.summarize(rows)
        assert out["headline"]["scope"] == "verified_90" and out["headline"]["n"] == 12
        assert (
            out["headline"]["hit_rate"] == pytest.approx(8 / 12, abs=1e-4)
            and out["headline"]["masked"] is False
        )
        groups = {
            (g["verification"], g["market"], g["horizon_days"]): g
            for g in out["groups"]
        }
        assert groups[("self_reported", "crypto", 90)] == {
            "verification": "self_reported",
            "market": "crypto",
            "horizon_days": 90,
            "n": 4,
            "masked": True,
            "needed": 6,
        }
        assert ("backfilled", "crypto", 90) not in groups
        assert out["counts"] == {
            "scored": 36,
            "pending": 3,
            "unscorable": 0,
            "backfilled": 20,
            "verified": 12,
            "calls": 0,
        }

    def test_headline_falls_back_and_masks_when_nothing_qualifies(self):
        rows = [
            {
                "status": "scored",
                "verification": "self_reported",
                "market": "tw_stock",
                "horizon_days": 30,
                "excess_pct": 1.0,
                "hit": True,
            }
            for _ in range(11)
        ]
        out = rules.summarize(rows)
        assert out["headline"]["scope"] == "all_30" and out["headline"]["n"] == 11
        out = rules.summarize(rows[:3])
        assert (
            out["headline"]["masked"] is True
            and out["headline"]["scope"] == "all_90"
            and out["headline"]["needed"] == 10
        )


# ── 服務（store 假造） ────────────────────────────────────────────────────────


class TestService:
    def test_build_pending_skips_known_and_unscorable(self, monkeypatch):
        trades = [
            {
                "id": 1,
                "symbol": "BTC",
                "market": "crypto",
                "side": "buy",
                "entry_type": "trade",
                "quantity": 1,
                "price": 60000,
                "traded_at": "2026-09-01",
                "created_at": "2026-09-01",
                "source": "onchain",
                "tx_hash": "0x1",
            },
            {
                "id": 2,
                "symbol": "TWD",
                "market": "cash",
                "side": "buy",
                "entry_type": "expense",
                "quantity": 1,
                "price": 100,
                "traded_at": "2026-09-01",
                "created_at": "2026-09-01",
            },
            {
                "id": 3,
                "symbol": "2330",
                "market": "tw_stock",
                "side": "sell",
                "entry_type": "trade",
                "quantity": 1,
                "price": 2400,
                "traded_at": "2026-09-02",
                "created_at": "2026-09-02",
                "deleted_at": "2026-09-05",
            },
        ]
        captured = {}
        monkeypatch.setattr(
            store, "eligible_trades", lambda uid, since_days=120: trades
        )
        monkeypatch.setattr(store, "existing_entry_ids", lambda uid: {1})
        monkeypatch.setattr(
            store,
            "insert_pending",
            lambda uid, rows: captured.setdefault("rows", rows) and len(rows),
        )
        n = service.build_pending_for_user("u1")
        assert n == 2  # 只有 id=3（兩個期限）；id=1 已有、id=2 不是交易；已刪除仍算
        assert {r["entry_id"] for r in captured["rows"]} == {3}

    def test_score_row_marks_scored_with_benchmark(self, monkeypatch):
        prices = {
            ("2330", "tw_stock", date(2026, 10, 1)): 2500.0,
            ("0050.TW", "tw_stock", date(2026, 9, 1)): 100.0,
            ("0050.TW", "tw_stock", date(2026, 10, 1)): 104.0,
        }
        saved = {}
        monkeypatch.setattr(
            store, "mark_scored", lambda rid, **kw: saved.update({"id": rid, **kw})
        )
        row = {
            "id": 9,
            "symbol": "2330",
            "market": "tw_stock",
            "side": "buy",
            "entry_price": "2400",
            "entry_date": date(2026, 9, 1),
            "exit_date": date(2026, 10, 1),
            "bench_symbol": "0050.TW",
            "attempts": 0,
        }
        assert (
            service.score_row(row, close_lookup=lambda s, m, d: prices.get((s, m, d)))
            == "scored"
        )
        assert saved["exit_price"] == 2500.0 and saved["raw_pct"] == pytest.approx(
            4.1667, abs=1e-3
        )
        assert (
            saved["bench_pct"] == 4.0
            and saved["excess_pct"] == pytest.approx(0.1667, abs=1e-3)
            and saved["hit"] is True
        )

    def test_score_row_counts_attempts_then_unscorable(self, monkeypatch):
        marks = []
        monkeypatch.setattr(
            store,
            "mark_attempt",
            lambda rid, attempts, note: marks.append((rid, attempts, note)),
        )
        row = {
            "id": 5,
            "symbol": "DEAD",
            "market": "crypto",
            "side": "buy",
            "entry_price": 1,
            "entry_date": date(2026, 9, 1),
            "exit_date": date(2026, 10, 1),
            "bench_symbol": "BTC",
            "attempts": 0,
        }
        assert service.score_row(row, close_lookup=lambda s, m, d: None) == "pending"
        row["attempts"] = 2
        assert service.score_row(row, close_lookup=lambda s, m, d: None) == "unscorable"
        assert marks[0][1] == 1 and marks[1][1] == 3 and "DEAD" in marks[1][2]

    def test_score_due_isolates_row_failures(self, monkeypatch):
        rows = [
            {
                "id": 1,
                "symbol": "A",
                "market": "crypto",
                "side": "buy",
                "entry_price": 1,
                "entry_date": date(2026, 9, 1),
                "exit_date": date(2026, 10, 1),
                "bench_symbol": None,
                "attempts": 0,
            }
            for _ in range(3)
        ]
        monkeypatch.setattr(
            store, "due_rows", lambda today, limit=500, user_id=None: rows
        )
        calls = {"n": 0}

        def flaky(s, m, d):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return 2.0

        monkeypatch.setattr(store, "mark_scored", lambda rid, **kw: None)
        out = service.score_due(date(2026, 10, 2), close_lookup=flaky)
        assert out == {"scored": 2, "pending": 0, "unscorable": 0, "errors": 1}

    def test_cron_isolates_user_failures(self, monkeypatch):
        from scripts import cron_judgment_scores as cron

        with (
            patch("core.scorecard.store.users_with_trades", return_value=["a", "b"]),
            patch(
                "core.scorecard.service.build_pending_for_user",
                side_effect=[2, RuntimeError("x")],
            ),
            patch(
                "core.scorecard.service.score_due",
                return_value={"scored": 1, "pending": 0, "unscorable": 0, "errors": 0},
            ),
        ):
            out = cron.run()
        assert (
            out["users"] == 2
            and out["created"] == 2
            and out["failed_users"] == 1
            and out["scored"] == 1
        )


# ── API ──────────────────────────────────────────────────────────────────────


def _client():
    from api.routers import scorecard as mod

    app = FastAPI()
    app.state.limiter = mod.limiter
    app.include_router(mod.router)

    async def fake_user():
        return {"user_id": "u1"}

    app.dependency_overrides[mod.get_current_user] = fake_user
    try:
        mod.limiter.reset()
    except Exception:  # noqa: BLE001
        pass
    return TestClient(app), mod


class TestApi:
    def test_get_and_entries_and_refresh(self):
        client, mod = _client()
        rows = [
            {
                "id": 1,
                "status": "scored",
                "verification": "verified",
                "market": "crypto",
                "horizon_days": 90,
                "excess_pct": 1.0,
                "hit": True,
                "symbol": "BTC",
                "side": "buy",
                "entry_date": "2026-06-01",
            }
        ]
        with patch.object(mod.store, "rows_for_user", return_value=rows):
            res = client.get("/api/journal/scorecard")
            assert (
                res.status_code == 200
                and res.json()["headline"]["masked"] is True
                and res.json()["recent"][0]["symbol"] == "BTC"
            )
            res = client.get("/api/journal/scorecard/entries?status=scored&limit=1")
            assert res.json()["count"] == 1 and res.json()["entries"][0]["id"] == 1
            assert (
                client.get("/api/journal/scorecard/entries?status=bogus").status_code
                == 422
            )
        with (
            patch.object(mod.service, "build_pending_for_user", return_value=4) as bp,
            patch.object(
                mod.service,
                "score_due_for_user",
                return_value={"scored": 1, "pending": 0, "unscorable": 0, "errors": 0},
            ) as sd,
            patch.object(mod.store, "rows_for_user", return_value=rows),
        ):
            res = client.post("/api/journal/scorecard/refresh")
        assert (
            res.status_code == 200
            and res.json()["created"] == 4
            and res.json()["scoring"]["scored"] == 1
        )
        bp.assert_called_once_with("u1")
        assert sd.call_args.args[0] == "u1"


# ── 工具 ─────────────────────────────────────────────────────────────────────


def test_tool_requires_login_and_returns_summary():
    from core.tools.scorecard_tool import get_my_scorecard

    with patch("core.tools.key_resolver.get_current_user_id", return_value=None):
        assert "error" in get_my_scorecard.invoke({})
    data = {
        "headline": {"scope": "all_90", "n": 0, "masked": True, "needed": 10},
        "groups": [],
        "counts": {"scored": 0},
        "min_sample": 10,
        "recent": [],
    }
    with (
        patch("core.tools.key_resolver.get_current_user_id", return_value="u1"),
        patch("core.scorecard.service.scorecard_for_user", return_value=data),
    ):
        out = get_my_scorecard.invoke({})
    assert out["headline"]["masked"] is True and "method" in out


# ── 守衛 ─────────────────────────────────────────────────────────────────────




@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales(lang):
    d = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    sc = d["journal"]["scorecard"]
    for key in (
        "title",
        "hint",
        "refresh",
        "failed",
        "empty",
        "hitRate",
        "avgExcess",
        "medianExcess",
        "scored",
        "pendingCount",
        "needMore",
        "verified",
        "selfReported",
        "byMarket",
        "recent",
        "long",
        "short",
        "scopeVerified90",
        "scopeAll90",
        "scopeAll30",
        "method",
        "refreshing",
        "refreshed",
        "tooSoon",
        "refreshFailed",
        "backfilled",
    ):
        assert key in sc, f"{lang} 缺 journal.scorecard.{key}"
    assert (
        "{n}" in sc["needMore"]
        and "{n}" in sc["pendingCount"]
        and "{v}" in sc["medianExcess"]
        and "{n}" in sc["method"]
    )
    for m in rules.SCORABLE_MARKETS:
        assert m in sc["markets"], f"{lang} 缺市場 {m}"
