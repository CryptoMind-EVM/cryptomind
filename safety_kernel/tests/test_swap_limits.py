"""swap_limits 單元測試：USD 換算、fail-closed 降級、超額拒絕、格式錯誤。

全離線——check_limit_with_price 是純決策，價格由測試直接給。
"""

from __future__ import annotations

import pytest

from safety_kernel.swap_limits import (
    NANO_PER_TON,
    SwapLimitCheck,
    check_limit_with_price,
)

# TON = $2.00 for deterministic math.
_TON_PRICE = 2.0
_MAX_USD = 50.0


class TestAllowed:
    def test_small_swap_allowed(self):
        # 10 TON = $20 < $50 → allowed
        result = check_limit_with_price(str(10 * NANO_PER_TON), _TON_PRICE, _MAX_USD)
        assert result.allowed
        assert result.input_usd == pytest.approx(20.0, abs=0.01)
        assert result.max_input_nano == 25 * NANO_PER_TON  # $50 / $2 = 25 TON

    def test_exactly_at_limit_allowed(self):
        # 25 TON = $50 = 上限 → allowed（邊界）
        result = check_limit_with_price(str(25 * NANO_PER_TON), _TON_PRICE, _MAX_USD)
        assert result.allowed
        assert result.input_usd == pytest.approx(50.0, abs=0.01)

    def test_returns_max_usd_in_result(self):
        result = check_limit_with_price(str(1 * NANO_PER_TON), _TON_PRICE, _MAX_USD)
        assert result.max_usd == 50.0


class TestRejected:
    def test_over_limit_rejected(self):
        # 30 TON = $60 > $50 → rejected，訊息含上限與建議數量
        result = check_limit_with_price(str(30 * NANO_PER_TON), _TON_PRICE, _MAX_USD)
        assert not result.allowed
        assert "50 USD" in result.reason
        assert "25.00 TON" in result.reason  # 建議上限

    def test_huge_amount_rejected(self):
        nano = str(1000 * NANO_PER_TON)  # 1000 TON = $2000
        result = check_limit_with_price(nano, _TON_PRICE, _MAX_USD)
        assert not result.allowed
        assert result.input_usd == pytest.approx(2000.0, abs=0.01)


class TestFailClosed:
    """查不到幣價必須拒絕（fail-closed），絕不放行未知金額。"""

    def test_no_price_rejects(self):
        result = check_limit_with_price(str(1 * NANO_PER_TON), None, _MAX_USD)
        assert not result.allowed
        assert "fail-closed" in result.reason

    def test_zero_price_rejects(self):
        result = check_limit_with_price(str(1 * NANO_PER_TON), 0, _MAX_USD)
        assert not result.allowed

    def test_negative_price_rejects(self):
        result = check_limit_with_price(str(1 * NANO_PER_TON), -1.0, _MAX_USD)
        assert not result.allowed


class TestValidation:
    def test_zero_rejected(self):
        result = check_limit_with_price("0", _TON_PRICE, _MAX_USD)
        assert not result.allowed
        assert "positive" in result.reason

    def test_negative_rejected(self):
        result = check_limit_with_price("-100", _TON_PRICE, _MAX_USD)
        assert not result.allowed

    def test_garbage_rejected(self):
        result = check_limit_with_price("abc", _TON_PRICE, _MAX_USD)
        assert not result.allowed
        assert "format" in result.reason

    def test_custom_max_usd_respected(self):
        # 上限是參數：規則不硬編碼 50 USD（營運可調），但行為一致。
        at_limit = check_limit_with_price(str(5 * NANO_PER_TON), _TON_PRICE, 10.0)
        assert at_limit.allowed  # 5 TON = $10 = 上限 → allowed（邊界）

        over = check_limit_with_price(str(6 * NANO_PER_TON), _TON_PRICE, 10.0)
        assert not over.allowed  # 6 TON = $12 > $10 → rejected


class TestSwapLimitCheckToDict:
    def test_serializes_for_api(self):
        check = SwapLimitCheck(
            allowed=True, max_usd=50.0, input_usd=20.0, max_input_nano=25_000_000_000
        )
        d = check.to_dict()
        assert d["allowed"] is True
        assert d["max_input_nano"] == "25000000000"  # string per Omniston convention
        assert d["input_usd"] == 20.0
