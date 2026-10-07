"""OKX client-side rate limiter + symbol cache + 429 handling 的單元測試。

驗證 data/data_fetcher.py 的 OkxDataFetcher 三層防禦：
1. check_symbol_availability 用批量快取取代逐個 instruments 預檢
2. _enforce_rate_limit 有 client-side throttle
3. _make_request 收到 429 時正確處理（Retry-After / 指數退避 / 中止）
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from data.data_fetcher import OkxDataFetcher, SymbolNotFoundError


@pytest.fixture
def fetcher():
    """全新 OkxDataFetcher，快取和 rate limiter 重置。"""
    return OkxDataFetcher()


# ─────────────────────────────────────────────────────────────────────────────
# 修復 1：check_symbol_availability 用批量快取
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_symbol_availability_uses_batch_cache(fetcher):
    """連續查 5 個幣只觸發 1 次批量 API（舊版會打 5 次逐個 instruments）。"""
    mock_data = [
        {"instId": "BTC-USDT", "state": "live"},
        {"instId": "ETH-USDT", "state": "live"},
        {"instId": "SOL-USDT", "state": "live"},
        {"instId": "DOGE-USDT", "state": "live"},
        {"instId": "ALLO-USDT", "state": "live"},
    ]
    with patch.object(fetcher, "_make_request", return_value=mock_data) as mock_req:
        # 連續查 5 個幣
        for sym in ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "ALLO-USDT"]:
            assert fetcher.check_symbol_availability(sym) is True

    # 只打了 1 次批量請求（不是 5 次逐個）
    assert mock_req.call_count == 1, (
        f"應只打 1 次批量 API，實際 {mock_req.call_count} 次"
    )


@pytest.mark.unit
def test_symbol_availability_cache_hit_no_api(fetcher):
    """快取有效期內，第二次查同一個幣不再打 API。"""
    mock_data = [{"instId": "BTC-USDT", "state": "live"}]
    with patch.object(fetcher, "_make_request", return_value=mock_data) as mock_req:
        fetcher.check_symbol_availability("BTC-USDT")
        fetcher.check_symbol_availability("BTC-USDT")
        fetcher.check_symbol_availability("BTC-USDT")

    assert mock_req.call_count == 1, "快取有效期內不應重打 API"


@pytest.mark.unit
def test_symbol_availability_not_found_raises(fetcher):
    """快取中沒有的幣 → SymbolNotFoundError。"""
    mock_data = [{"instId": "BTC-USDT", "state": "live"}]
    with patch.object(fetcher, "_make_request", return_value=mock_data):
        with pytest.raises(SymbolNotFoundError):
            fetcher.check_symbol_availability("FAKE-USDT")


@pytest.mark.unit
def test_symbol_availability_cache_empty_falls_through(fetcher):
    """批量請求失敗（快取空）→ 放行讓下游 candles 自然處理，不卡住。"""
    with patch.object(fetcher, "_make_request", return_value=None):
        result = fetcher.check_symbol_availability("BTC-USDT")
    assert result is True, "快取空時應放行（不阻擋後續 candles 請求）"


# ─────────────────────────────────────────────────────────────────────────────
# 修復 2：client-side rate limiter
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_rate_limiter_enforces_min_interval(fetcher):
    """最小間隔 100ms — 連續呼叫第二次應被 throttle。"""
    import time

    fetcher._min_request_interval = 0.05  # 測試用 50ms 加速
    fetcher._last_request_time = time.time()

    start = time.monotonic()
    fetcher._enforce_rate_limit()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.04, f"應被 throttle 至少 ~50ms，實際 {elapsed:.3f}s"


@pytest.mark.unit
def test_rate_limiter_window_reset(fetcher):
    """超過分鐘上限 → 等待窗口重置（用 mock time 加速）。"""
    import time

    fetcher._max_requests_per_minute = 3
    fetcher._request_count_in_window = 3  # 已達上限
    fetcher._request_window_start = time.time() - 61  # 窗口已過期

    # 窗口已過期 → 不應等待，直接重置
    start = time.monotonic()
    fetcher._enforce_rate_limit()
    elapsed = time.monotonic() - start

    assert elapsed < 0.2, "窗口已過期應立即重置，不等待"
    assert fetcher._request_count_in_window == 1


# ─────────────────────────────────────────────────────────────────────────────
# 修復 3：429 特殊處理
# ─────────────────────────────────────────────────────────────────────────────


def _make_response(status_code, headers=None, json_body=None):
    """建立 mock requests.Response。"""
    resp = requests.Response()
    resp.status_code = status_code
    if headers:
        resp.headers.update(headers)
    if json_body is not None:
        import json

        resp._content = json.dumps(json_body).encode()
    return resp


@pytest.mark.unit
def test_429_reads_retry_after_header(fetcher):
    """429 帶 Retry-After → 等待該秒數後重試。"""
    call_count = {"n": 0}

    def mock_get(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_response(429, headers={"Retry-After": "0.01"})
        return _make_response(200, json_body={"code": "0", "data": [{"test": 1}]})

    with patch("data.data_fetcher.requests.get", side_effect=mock_get):
        with patch.object(fetcher, "_enforce_rate_limit"):  # 跳過 throttle
            result = fetcher._make_request("/test")

    assert call_count["n"] == 2, "429 後應重試一次"
    assert result == [{"test": 1}]


@pytest.mark.unit
def test_429_exponential_backoff_without_retry_after(fetcher):
    """429 無 Retry-After → 指數退避（不是舊版的 1s/2s）。"""
    sleep_calls = []

    resp_429 = _make_response(429)
    resp_429.raise_for_status = lambda: (_ for _ in ()).throw(
        requests.exceptions.HTTPError(response=resp_429)
    )

    with patch("data.data_fetcher.requests.get", return_value=resp_429):
        with patch.object(fetcher, "_enforce_rate_limit"):
            with patch("data.data_fetcher.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                result = fetcher._make_request("/test")

    assert result is None  # 3 次都 429 → 放棄
    # 第一次退避應 ≥ 10s（指數退避起點），不是舊版的 1s
    assert sleep_calls[0] >= 10, (
        f"429 退避起點應 ≥10s，實際 {sleep_calls[0]}"
    )


@pytest.mark.unit
def test_418_aborts_immediately(fetcher):
    """HTTP 418（IP ban）→ 立即中止，不重試（避免 ban 升級）。"""
    call_count = {"n": 0}

    resp_418 = _make_response(418)
    resp_418.raise_for_status = lambda: (_ for _ in ()).throw(
        requests.exceptions.HTTPError(response=resp_418)
    )

    def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return resp_418

    with patch("data.data_fetcher.requests.get", side_effect=mock_get):
        with patch.object(fetcher, "_enforce_rate_limit"):
            result = fetcher._make_request("/test")

    assert result is None
    assert call_count["n"] == 1, "418 應立即中止，只打 1 次（不重試）"
