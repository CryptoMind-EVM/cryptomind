"""Tests for price alert background checker."""


class TestIsConditionMet:
    """Test the condition evaluation logic."""

    def test_above_triggered(self):
        from api.alert_checker import is_condition_met

        assert (
            is_condition_met("above", 200.0, current_price=205.0, open_price=195.0)
            is True
        )

    def test_above_not_triggered(self):
        from api.alert_checker import is_condition_met

        assert (
            is_condition_met("above", 200.0, current_price=195.0, open_price=190.0)
            is False
        )

    def test_below_triggered(self):
        from api.alert_checker import is_condition_met

        assert (
            is_condition_met("below", 180.0, current_price=175.0, open_price=185.0)
            is True
        )

    def test_below_not_triggered(self):
        from api.alert_checker import is_condition_met

        assert (
            is_condition_met("below", 180.0, current_price=185.0, open_price=182.0)
            is False
        )

    def test_change_pct_up_triggered(self):
        from api.alert_checker import is_condition_met

        # 10% up: current=110, open=100
        assert (
            is_condition_met(
                "change_pct_up", 5.0, current_price=110.0, open_price=100.0
            )
            is True
        )

    def test_change_pct_up_not_triggered(self):
        from api.alert_checker import is_condition_met

        # 3% up: current=103, open=100
        assert (
            is_condition_met(
                "change_pct_up", 5.0, current_price=103.0, open_price=100.0
            )
            is False
        )

    def test_change_pct_down_triggered(self):
        from api.alert_checker import is_condition_met

        # 10% down: current=90, open=100
        assert (
            is_condition_met(
                "change_pct_down", 5.0, current_price=90.0, open_price=100.0
            )
            is True
        )

    def test_zero_open_price_returns_false(self):
        from api.alert_checker import is_condition_met

        assert (
            is_condition_met("change_pct_up", 5.0, current_price=105.0, open_price=0.0)
            is False
        )


class TestBuildAlertMessage:
    def test_above_message(self):
        from api.alert_checker import build_alert_body

        alert = {"symbol": "AAPL", "condition": "above", "target": 200.0}
        msg = build_alert_body(alert, current_price=205.50)
        assert "AAPL" in msg
        assert "205" in msg

    def test_change_pct_message(self):
        from api.alert_checker import build_alert_body

        alert = {"symbol": "BTC", "condition": "change_pct_up", "target": 5.0}
        msg = build_alert_body(alert, current_price=55000.0)
        assert "BTC" in msg


class TestRepeatAlertRearm:
    """重複警報：穿越時響一次，條件持續成立不再響，條件解除才重新上膛。

    回歸鎖：triggered 寫了沒人讀，repeat 警報站上目標價期間每 60 秒響一次。
    """

    def _run(self, alert, price):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from api import alert_checker

        mark = MagicMock()
        rearm = MagicMock()
        push = AsyncMock()
        with (
            patch.object(
                alert_checker, "_fetch_price", AsyncMock(return_value=(price, price))
            ),
            patch.object(
                alert_checker, "_create_notification_sync", return_value={"id": 1}
            ),
            patch("api.routers.notifications.push_notification_to_user", push),
            patch("core.database.mark_alert_triggered", mark),
            patch("core.database.rearm_alert", rearm),
        ):
            fired = asyncio.run(alert_checker._check_single_alert(alert))
        return fired, mark, rearm, push

    @staticmethod
    def _alert(repeat, triggered):
        return {
            "id": "a1",
            "user_id": "u1",
            "symbol": "BTC",
            "market": "crypto",
            "condition": "above",
            "target": 100.0,
            "repeat": repeat,
            "triggered": triggered,
        }

    def test_repeat_fires_on_first_cross(self):
        fired, mark, rearm, push = self._run(self._alert(1, 0), 105.0)
        assert fired is True
        mark.assert_called_once_with("a1", True)
        push.assert_awaited_once()
        rearm.assert_not_called()

    def test_repeat_already_fired_and_still_met_stays_quiet(self):
        fired, mark, rearm, push = self._run(self._alert(1, 1), 110.0)
        assert fired is False
        mark.assert_not_called()
        push.assert_not_awaited()
        rearm.assert_not_called()

    def test_repeat_rearms_when_condition_clears(self):
        fired, mark, rearm, push = self._run(self._alert(1, 1), 95.0)
        assert fired is False
        rearm.assert_called_once_with("a1")
        push.assert_not_awaited()

    def test_repeat_armed_and_not_met_does_nothing(self):
        fired, mark, rearm, push = self._run(self._alert(1, 0), 95.0)
        assert fired is False
        rearm.assert_not_called()
        mark.assert_not_called()

    def test_one_shot_unchanged(self):
        fired, mark, rearm, push = self._run(self._alert(0, 0), 105.0)
        assert fired is True
        mark.assert_called_once_with("a1", False)
        rearm.assert_not_called()


class TestRearmAlertSql:
    def test_rearm_resets_only_repeat_alerts(self):
        from unittest.mock import MagicMock, patch

        from core.database.price_alerts import rearm_alert

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "core.database.price_alerts.get_connection", return_value=mock_conn
        ):
            rearm_alert("a1")

        sql, params = mock_cursor.execute.call_args[0]
        assert "SET triggered = 0" in sql and "repeat = 1" in sql
        assert params == ("a1",)
        mock_conn.commit.assert_called_once()
