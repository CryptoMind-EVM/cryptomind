"""每日早報 cron 的接線與批次行為（不打 DB、不送 Telegram）。"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.daily_brief.compose import BriefData
from core.daily_brief.schedule import effective_prefs

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)  # 台北 08:05


def _row(uid="u1", **kw):
    base = {
        "user_id": uid,
        "language": "zh-TW",
        "display_name": "D",
        "membership_tier": "free",
        "telegram_id": 111,
        "prefs_user_id": None,
        # 測試裡給了 prefs_user_id 的列都當成使用者自己設的（c063）；自動建的列另外測
        "user_set": True,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "last_sent_on": None,
    }
    base.update(kw)
    return base


@pytest.fixture
def claim_ok():
    """這幾組測試不打 DB：今天這一份一律搶得到（搶占本身在 TestClaimDay 與真 DB 測試驗）"""
    with (
        patch("core.daily_brief.store.claim_day", return_value=True),
        patch("core.daily_brief.store.release_day"),
    ):
        yield


def _data(**kw):
    d = BriefData(language="zh-TW", date_local=NOW.date(), display_name="D")
    for k, v in kw.items():
        setattr(d, k, v)
    return d


@pytest.mark.usefixtures("claim_ok")
class TestSendOne:
    async def test_sends_to_telegram_and_inapp_then_marks_sent(self):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        data = _data(
            positions=[{"symbol": "BTC", "market": "crypto", "change_pct": -2.1}]
        )
        tg = AsyncMock(return_value=True)
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=data),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value="BTC -2.1%，要看支撐嗎？"),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=tg),
            patch("core.daily_brief.send.send_inapp", return_value=True) as inapp,
            patch("core.daily_brief.store.mark_sent") as mark,
        ):
            assert await send_one(prefs, NOW) == "sent"
        text = tg.call_args.args[1]
        assert tg.call_args.args[0] == 111
        assert "📊" in text and "🤖 一句話：BTC -2.1%" in text
        inapp.assert_called_once()
        mark.assert_called_once()
        assert (
            mark.call_args.args[1] == NOW.astimezone().date()
            or mark.call_args.args[1].isoformat() == "2026-09-13"
        )

    async def test_empty_brief_is_not_sent_but_still_marked(self):
        """沒內容不推垃圾訊息，但要記今天，避免每小時重試。"""
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=_data()),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=AsyncMock()) as tg,
            patch("core.daily_brief.store.mark_sent") as mark,
        ):
            assert await send_one(prefs, NOW) == "empty"
        tg.assert_not_called()
        mark.assert_called_once()

    async def test_no_insight_still_sends(self):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, channels=["telegram"])
        )
        data = _data(alerts=[{"title": "x", "body": "BTC < 60,000"}])
        tg = AsyncMock(return_value=True)
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=data),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=tg),
            patch("core.daily_brief.send.send_inapp") as inapp,
            patch("core.daily_brief.store.mark_sent"),
        ):
            assert await send_one(prefs, NOW) == "sent"
        assert "🤖" not in tg.call_args.args[1]
        inapp.assert_not_called()  # channels 只有 telegram

    async def test_dry_run_prints_and_does_not_send(self, capsys):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        data = _data(alerts=[{"title": "x", "body": "BTC < 60,000"}])
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=data),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=AsyncMock()) as tg,
            patch("core.daily_brief.store.mark_sent") as mark,
        ):
            assert await send_one(prefs, NOW, dry_run=True) == "sent"
        tg.assert_not_called()
        mark.assert_not_called()
        assert "BTC < 60,000" in capsys.readouterr().out


@pytest.mark.usefixtures("claim_ok")
class TestRun:
    async def test_only_due_users_processed_and_one_failure_does_not_stop_batch(self):
        from scripts import cron_daily_brief as mod

        rows = [
            _row("u1"),
            _row("u2"),
            _row("u3", language="en"),
        ]  # u3 是 UTC，00:05 未到點

        async def fake_send_one(prefs, now_utc, dry_run=False, force=False):
            if prefs.user_id == "u1":
                raise RuntimeError("boom")
            return "sent"

        with (
            patch("core.daily_brief.store.list_candidates", return_value=rows),
            patch.object(mod, "send_one", new=fake_send_one),
        ):
            summary = await mod.run(NOW)
        assert summary == {
            "candidates": 3,
            "due": 2,
            "sent": 1,
            "empty": 0,
            "failed": 1,
            "skipped": 0,
        }

    async def test_force_single_user_ignores_schedule(self):
        from scripts import cron_daily_brief as mod

        with (
            patch(
                "core.daily_brief.store.get_prefs_row",
                return_value=_row("u9", language="en"),
            ),
            patch.object(mod, "send_one", new=AsyncMock(return_value="sent")) as so,
        ):
            summary = await mod.run(NOW, only_user="u9", force=True)
        assert summary["due"] == 1 and summary["sent"] == 1
        assert so.call_args.args[0].user_id == "u9"


@pytest.mark.usefixtures("claim_ok")
class TestConcurrency:
    """每人一次 LLM 呼叫，逐一跑的話人數一多就跑超過一小時的排程（2026-09-25）。"""

    @staticmethod
    def _tracking_send_one(fail_uid=None, delay=0.02):
        import asyncio

        state = {"in_flight": 0, "peak": 0, "seen": []}

        async def fake_send_one(prefs, now_utc, dry_run=False, force=False):
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            try:
                await asyncio.sleep(delay)
                state["seen"].append(prefs.user_id)
                if prefs.user_id == fail_uid:
                    raise RuntimeError("boom")
                return "sent"
            finally:
                state["in_flight"] -= 1

        return fake_send_one, state

    async def test_bounded_by_env_and_one_failure_does_not_stop_others(
        self, monkeypatch
    ):
        from scripts import cron_daily_brief as mod

        monkeypatch.setenv("DAILY_BRIEF_CONCURRENCY", "2")
        rows = [_row(f"u{i}") for i in range(6)]
        fake, state = self._tracking_send_one(fail_uid="u1")
        with (
            patch("core.daily_brief.store.list_candidates", return_value=rows),
            patch.object(mod, "send_one", new=fake),
        ):
            summary = await mod.run(NOW)
        assert state["peak"] == 2
        assert sorted(state["seen"]) == [f"u{i}" for i in range(6)]
        assert summary == {
            "candidates": 6,
            "due": 6,
            "sent": 5,
            "empty": 0,
            "failed": 1,
            "skipped": 0,
        }
        assert mod.exit_code(summary) == 0

    async def test_default_is_four(self, monkeypatch):
        from scripts import cron_daily_brief as mod

        monkeypatch.delenv("DAILY_BRIEF_CONCURRENCY", raising=False)
        rows = [_row(f"u{i}") for i in range(10)]
        fake, state = self._tracking_send_one()
        with (
            patch("core.daily_brief.store.list_candidates", return_value=rows),
            patch.object(mod, "send_one", new=fake),
        ):
            summary = await mod.run(NOW)
        assert state["peak"] == 4
        assert summary["sent"] == 10

    async def test_all_failed_still_exits_non_zero(self, monkeypatch):
        from scripts import cron_daily_brief as mod

        async def always_fail(prefs, now_utc, dry_run=False):
            raise RuntimeError("down")

        with (
            patch(
                "core.daily_brief.store.list_candidates",
                return_value=[_row("u1"), _row("u2")],
            ),
            patch.object(mod, "send_one", new=always_fail),
        ):
            summary = await mod.run(NOW)
        assert summary["failed"] == 2 and mod.exit_code(summary) == 1

    @pytest.mark.parametrize(
        "raw, expected",
        [("8", 8), ("1", 1), ("0", 1), ("-3", 1), ("abc", 4), ("", 4), ("999", 16)],
    )
    def test_env_parsing(self, monkeypatch, raw, expected):
        from scripts import cron_daily_brief as mod

        monkeypatch.setenv("DAILY_BRIEF_CONCURRENCY", raw)
        assert mod.concurrency() == expected


class TestKillSwitch:
    def test_env_off_skips_batch(self, monkeypatch):
        from scripts import cron_daily_brief as mod

        monkeypatch.setenv("DAILY_BRIEF_ENABLED", "false")
        called = []
        monkeypatch.setattr(
            mod.asyncio, "run", lambda coro: (called.append(1), coro.close(), {})[-1]
        )
        assert mod.main([]) == 0
        assert not called, "開關關掉時不該進批次"

    @pytest.mark.skipif(sys.platform == "win32", reason="core/cron_lock 用 fcntl（Linux 專用）")
    def test_env_off_still_allows_single_user(self, monkeypatch):
        from scripts import cron_daily_brief as mod

        monkeypatch.setenv("DAILY_BRIEF_ENABLED", "0")
        called = []
        monkeypatch.setattr(
            mod.asyncio, "run", lambda coro: (called.append(1), coro.close(), {})[-1]
        )
        assert mod.main(["--user", "u1", "--dry-run"]) == 0
        assert called, "--user 單人試送不受開關影響"

    def test_default_on(self, monkeypatch):
        from scripts import cron_daily_brief as mod

        monkeypatch.delenv("DAILY_BRIEF_ENABLED", raising=False)
        assert mod.cron_enabled() is True


class TestSeams:


    def test_schema_registers_tables_and_migration_chains(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("daily_brief", create_daily_brief_tables)' in schema
        assert "CREATE TABLE IF NOT EXISTS user_brief_prefs" in schema
        assert "CREATE TABLE IF NOT EXISTS user_calendar_events" in schema
        mig = (REPO / "alembic" / "versions" / "c045_daily_brief.py").read_text(
            encoding="utf-8"
        )
        assert 'revision = "c045"' in mig and 'down_revision = "c044"' in mig

    def test_alert_notifications_are_the_alert_source(self):
        """設計文件原本要加 price_alerts.triggered_at；改讀 notifications（一次性警報觸發即刪）。"""
        store = (REPO / "core" / "daily_brief" / "store.py").read_text(encoding="utf-8")
        assert "type = 'price_alert'" in store
        checker = (REPO / "api" / "alert_checker.py").read_text(encoding="utf-8")
        assert 'notification_type="price_alert"' in checker


class TestClaimDay:
    """2026-09-29：9/27 同一人收到兩份早報。cron 的檔案鎖只管同一個容器，部署交接時新舊
    容器重疊（或手動 --force）就兩邊都送；而且 last_sent_on 是送完才記，中間隔著 LLM
    好幾分鐘。改成產生內容前先在 DB 原子地「搶下今天這一份」，搶不到就跳過。"""

    async def test_not_claimed_means_skip_and_send_nothing(self):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        collect = AsyncMock(return_value=_data(positions=[{"symbol": "BTC", "market": "crypto", "change_pct": 1.0}]))
        with (
            patch("core.daily_brief.store.claim_day", return_value=False) as claim,
            patch("core.daily_brief.collect.collect_brief_data", new=collect),
            patch("core.daily_brief.send.send_telegram_text", new=AsyncMock()) as tg,
        ):
            assert await send_one(prefs, NOW) == "skipped"
        claim.assert_called_once()
        collect.assert_not_called()
        tg.assert_not_called()

    async def test_failure_before_delivery_releases_the_claim(self):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(
            _row(prefs_user_id="u1", last_sent_on=datetime(2026, 9, 12).date())
        )
        with (
            patch("core.daily_brief.store.claim_day", return_value=True),
            patch("core.daily_brief.store.release_day") as release,
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(side_effect=RuntimeError("quote api down")),
            ),
        ):
            with pytest.raises(RuntimeError):
                await send_one(prefs, NOW)
        release.assert_called_once()
        assert release.call_args.args[0] == "u1"
        assert release.call_args.args[2] == datetime(2026, 9, 12).date(), "還回去成原本的值"

    async def test_failure_after_delivery_keeps_the_claim(self):
        """已經送到任何一個管道就不能還回去——還了下一輪會再送一次"""
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        data = _data(positions=[{"symbol": "BTC", "market": "crypto", "change_pct": 1.0}])
        with (
            patch("core.daily_brief.store.claim_day", return_value=True),
            patch("core.daily_brief.store.release_day") as release,
            patch("core.daily_brief.collect.collect_brief_data", new=AsyncMock(return_value=data)),
            patch("core.daily_brief.insight.generate_insight", new=AsyncMock(return_value=None)),
            patch("core.daily_brief.send.send_telegram_text", new=AsyncMock(return_value=True)),
            patch("core.daily_brief.store.mark_sent", side_effect=RuntimeError("db gone")),
        ):
            with pytest.raises(RuntimeError):
                await send_one(prefs, NOW)
        release.assert_not_called()

    async def test_force_and_dry_run_do_not_claim(self):
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(_row())
        with (
            patch("core.daily_brief.store.claim_day") as claim,
            patch("core.daily_brief.collect.collect_brief_data", new=AsyncMock(return_value=_data())),
            patch("core.daily_brief.store.mark_sent"),
        ):
            await send_one(prefs, NOW, force=True)
            await send_one(prefs, NOW, dry_run=True)
        claim.assert_not_called()

    async def test_run_passes_force_through(self):
        from scripts import cron_daily_brief as mod

        with (
            patch("core.daily_brief.store.get_prefs_row", return_value=_row("u9")),
            patch.object(mod, "send_one", new=AsyncMock(return_value="sent")) as so,
        ):
            await mod.run(NOW, only_user="u9", force=True)
        assert so.call_args.kwargs.get("force") is True


def test_claim_day_is_atomic_per_user_and_date():
    """真 PostgreSQL：同一天只有第一個搶得到；還回去可以再搶；隔天是新的一份"""
    import uuid
    from datetime import date

    from core.daily_brief import store

    try:
        from core.database.connection import get_connection

        conn = get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    uid = f"t-claim-{uuid.uuid4().hex[:8]}"
    day = date(2026, 9, 27)
    try:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid))
        conn.commit()

        assert store.claim_day(uid, day, timezone="Asia/Taipei") is True
        assert store.claim_day(uid, day, timezone="Asia/Taipei") is False, "第二個容器搶不到"
        store.release_day(uid, day, None)
        assert store.claim_day(uid, day) is True, "還回去之後可以再搶"
        assert store.claim_day(uid, date(2026, 9, 28)) is True, "隔天是新的一份"
        store.release_day(uid, day, None)  # 已經不是 9/27 了：不能把 9/28 還掉
        assert store.claim_day(uid, date(2026, 9, 28)) is False
    finally:
        c = conn.cursor()
        c.execute("DELETE FROM user_brief_prefs WHERE user_id = %s", (uid,))
        c.execute("DELETE FROM users WHERE user_id = %s", (uid,))
        conn.commit()
        conn.close()


def test_only_user_writes_mark_prefs_as_user_set():
    """真 PostgreSQL（c063）：mark_sent／claim_day 自動建的列是記帳用（user_set=FALSE）；
    設定頁 upsert_prefs、bot set_enabled、Email 訂閱確認才算使用者自己設。
    自動建的列被 set_enabled／Email 確認接手時，頻道要從程式預設開始（DB 預設少了 baseapp）。"""
    import uuid
    from datetime import date

    from core.daily_brief import store
    from core.daily_brief.schedule import DEFAULT_CHANNELS
    from core.database.base import DatabaseBase
    from core.email_brief import store as email_store

    try:
        from core.database.connection import get_connection

        conn = get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    suffix = uuid.uuid4().hex[:8]
    auto, bot, page, mail = (f"t-uset-{k}-{suffix}" for k in ("auto", "bot", "page", "mail"))

    def row(uid):
        return DatabaseBase.query_one(
            "SELECT user_set, channels FROM user_brief_prefs WHERE user_id = %s", (uid,)
        )

    try:
        c = conn.cursor()
        for uid in (auto, bot, page, mail):
            c.execute("INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid))
        conn.commit()

        store.mark_sent(auto, date(2026, 9, 27))
        assert row(auto)["user_set"] is False
        assert store.claim_day(auto, date(2026, 9, 28)) is True
        assert row(auto)["user_set"] is False, "記帳不會把它變成自己設的"

        store.claim_day(bot, date(2026, 9, 27))
        store.set_enabled(bot, True)
        assert row(bot)["user_set"] is True
        assert list(row(bot)["channels"]) == list(DEFAULT_CHANNELS), "接手自動建的列要補上 baseapp"
        store.upsert_prefs(bot, enabled=True, send_hour=9, timezone="UTC", channels=["inapp"], include_spend=True)
        store.set_enabled(bot, False)
        assert list(row(bot)["channels"]) == ["inapp"], "自己設過的頻道不能被蓋掉"
        store.claim_day(bot, date(2026, 9, 29))
        assert row(bot)["user_set"] is True, "之後記帳也不會把它改回去"

        store.upsert_prefs(page, enabled=True, send_hour=8, timezone="Asia/Taipei", channels=["telegram", "inapp"], include_spend=True)
        assert row(page)["user_set"] is True

        store.mark_sent(mail, date(2026, 9, 27))
        confirm_hash = uuid.uuid4().hex
        email_store.upsert_pending(mail, f"{mail}@example.com", confirm_hash, uuid.uuid4().hex)
        assert email_store.mark_verified(mail, confirm_hash, "Asia/Taipei", list(DEFAULT_CHANNELS)) is True
        assert row(mail)["user_set"] is True
        assert list(row(mail)["channels"]) == [*DEFAULT_CHANNELS, "email"], "接手自動建的列要補上 baseapp"
    finally:
        c = conn.cursor()
        for uid in (auto, bot, page, mail):
            c.execute("DELETE FROM user_email_subscriptions WHERE user_id = %s", (uid,))
            c.execute("DELETE FROM user_brief_prefs WHERE user_id = %s", (uid,))
            c.execute("DELETE FROM users WHERE user_id = %s", (uid,))
        conn.commit()
        conn.close()


def test_c063_backfill_marks_only_rows_that_look_user_set():
    """真 PostgreSQL：回填寧可少送——值跟 DB 預設一樣、綁了 Telegram、沒有啟用中 Base App token
    的才當自動建的；其他都算自己設過。只 SELECT 回填條件（不 UPDATE 整張表），交易內跑完 rollback。"""
    import importlib.util
    import uuid

    spec = importlib.util.spec_from_file_location(
        "c063", REPO / "alembic" / "versions" / "c063_brief_prefs_user_set.py"
    )
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    try:
        from core.database.connection import get_connection

        conn = get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    s = uuid.uuid4().hex[:8]
    tg = iter(range(9_900_000_000 + int(s, 16) % 1_000_000, 9_999_999_999))
    cases = {
        # user_id 後綴: (prefs 覆寫, 綁 TG, 有 Base App token, 預期)
        "auto": ({}, True, False, False),
        "off": ({"enabled": False}, True, False, True),
        "hour": ({"send_hour": 7}, True, False, True),
        "chan": ({"channels": ["telegram", "inapp", "baseapp"]}, True, False, True),
        "macro": ({"include_macro": False}, True, False, True),
        "notg": ({}, False, False, True),
        "base": ({}, True, True, True),
        "gone": ({}, True, "disabled", False),  # review：取消 Base App 通知的不算活躍
    }
    try:
        c = conn.cursor()
        for key, (over, bound, token, _) in cases.items():
            uid = f"t-c063-{key}-{s}"
            c.execute("INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid))
            cols = {"enabled": True, "send_hour": 8, "channels": ["telegram", "inapp"],
                    "include_spend": True, "include_macro": True, **over}
            c.execute(
                "INSERT INTO user_brief_prefs (user_id, enabled, send_hour, channels, include_spend, include_macro)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (uid, cols["enabled"], cols["send_hour"], cols["channels"], cols["include_spend"], cols["include_macro"]),
            )
            if bound:
                c.execute("INSERT INTO telegram_bindings (telegram_id, user_id) VALUES (%s, %s)", (next(tg), uid))
            if token:
                c.execute(
                    "INSERT INTO miniapp_notification_tokens (fid, user_id, url, token, enabled)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (1, uid, f"https://example.com/n/{uid}", uuid.uuid4().hex, token is True),
                )
        c.execute(
            f"SELECT p.user_id, {mig.LOOKS_USER_SET} FROM user_brief_prefs p WHERE p.user_id LIKE %s",
            (f"t-c063-%-{s}",),
        )
        got = {uid.split("-")[2]: v for uid, v in c.fetchall()}
        assert got == {k: v[3] for k, v in cases.items()}
    finally:
        conn.rollback()
        conn.close()
