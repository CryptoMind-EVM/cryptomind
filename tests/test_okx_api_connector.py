"""
Tests for OKX API connector in utils/okx_api_connector.py
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.okx_api_connector import OKXAPIConnector


class TestOKXAPIConnectorInit:
    def test_init_defaults_empty_credentials(self):
        connector = OKXAPIConnector()
        assert connector.api_key == ""
        assert connector.secret_key == ""
        assert connector.passphrase == ""

    def test_custom_base_url(self):
        with patch.dict("os.environ", {"OKX_BASE_URL": "https://custom.okx.com"}):
            connector = OKXAPIConnector()
            assert connector.base_url == "https://custom.okx.com"

    def test_default_base_url(self):
        with patch.dict("os.environ", {}, clear=True):
            connector = OKXAPIConnector()
            assert connector.base_url == "https://www.okx.com"

    def test_public_endpoints_defined(self):
        connector = OKXAPIConnector()
        assert len(connector.public_endpoints) > 0
        assert "/market/ticker" in connector.public_endpoints


class TestGenerateSignature:
    def test_empty_signature_without_secret(self):
        connector = OKXAPIConnector()
        signature = connector._generate_signature(
            "2024-01-01T00:00:00.000Z", "GET", "/api/v5/test"
        )
        assert signature == ""

    def test_signature_with_manually_set_credentials(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        timestamp = "2024-01-01T00:00:00.000Z"
        signature = connector._generate_signature(
            timestamp, "GET", "/api/v5/account/balance"
        )
        assert signature
        assert isinstance(signature, str)

    def test_different_timestamps_different_signatures(self):
        connector = OKXAPIConnector()
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        sig1 = connector._generate_signature(
            "2024-01-01T00:00:00.000Z", "GET", "/api/v5/test"
        )
        sig2 = connector._generate_signature(
            "2024-01-01T00:00:01.000Z", "GET", "/api/v5/test"
        )
        assert sig1 != sig2

    def test_different_methods_different_signatures(self):
        connector = OKXAPIConnector()
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        timestamp = "2024-01-01T00:00:00.000Z"
        sig_get = connector._generate_signature(timestamp, "GET", "/api/v5/test")
        sig_post = connector._generate_signature(timestamp, "POST", "/api/v5/test")
        assert sig_get != sig_post

    def test_different_paths_different_signatures(self):
        connector = OKXAPIConnector()
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        timestamp = "2024-01-01T00:00:00.000Z"
        sig1 = connector._generate_signature(timestamp, "GET", "/api/v5/path1")
        sig2 = connector._generate_signature(timestamp, "GET", "/api/v5/path2")
        assert sig1 != sig2


class TestMakeRequest:
    def test_public_endpoint_without_credentials(self):
        connector = OKXAPIConnector()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT"}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector._make_request(
                "GET", "/market/ticker", params={"instId": "BTC-USDT"}
            )
            assert result["code"] == "0"

    def test_private_endpoint_without_credentials_returns_error(self):
        connector = OKXAPIConnector()
        result = connector._make_request("GET", "/account/balance")
        assert result["code"] == "50000"
        assert "未設置" in result["msg"]

    def test_private_endpoint_with_credentials(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "0", "data": []}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector._make_request("GET", "/account/balance")
            assert result["code"] == "0"

    def test_get_request_with_params(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "0", "data": []}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector._make_request(
                "GET", "/market/ticker", params={"instId": "BTC-USDT"}
            )
            assert result["code"] == "0"

    def test_post_request_with_data(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "0", "data": {}}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = connector._make_request(
                "POST", "/trade/order", data={"instId": "BTC-USDT"}
            )
            assert result["code"] == "0"

    def test_handles_request_exception(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        with patch("requests.get", side_effect=Exception("Network error")):
            result = connector._make_request("GET", "/market/ticker")
            assert "code" in result


class TestAccountMethods:
    def test_get_account_balance(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"ccy": "USDT", "bal": "1000.00"}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector.get_account_balance("USDT")
            assert result["code"] == "0"

    def test_get_positions(self):
        connector = OKXAPIConnector()
        connector.api_key = "test-key"
        connector.secret_key = "test-secret-key-12345678901234567890"
        connector.passphrase = "test-pass"

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "0", "data": []}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector.get_positions()
            assert result["code"] == "0"


class TestMarketDataMethods:
    def test_get_instruments(self):
        connector = OKXAPIConnector()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT"}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector.get_instruments("SPOT")
            assert result["code"] == "0"

    def test_get_ticker(self):
        connector = OKXAPIConnector()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT", "last": "50000"}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector.get_ticker("BTC-USDT")
            assert result["code"] == "0"

    def test_get_tickers(self):
        connector = OKXAPIConnector()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT"}, {"instId": "ETH-USDT"}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = connector.get_tickers("SPOT")
            assert result["code"] == "0"


class TestConnection:
    def test_test_connection_success(self):
        connector = OKXAPIConnector()

        with patch.object(
            connector,
            "get_account_balance",
            return_value={"code": "0", "data": []},
        ):
            result = connector.test_connection()
            assert result is True

    def test_test_connection_failure(self):
        connector = OKXAPIConnector()

        with patch.object(
            connector,
            "get_account_balance",
            return_value={"code": "50113", "msg": "Invalid sign"},
        ):
            result = connector.test_connection()
            assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# Client-side rate limiter + 429/418 handling（funding rate 爆量防護）
# ─────────────────────────────────────────────────────────────────────────────


class TestRateLimiter:
    """OKXAPIConnector client-side rate limiter。"""

    def test_enforces_min_interval(self):
        import time

        connector = OKXAPIConnector()
        connector._rl_min_interval = 0.05  # 50ms 加速測試
        connector._rl_last_request = time.time()

        start = time.monotonic()
        connector._enforce_rate_limit()
        elapsed = time.monotonic() - start

        assert elapsed >= 0.04, f"應 throttle ~50ms，實際 {elapsed:.3f}s"

    def test_counter_increments(self):
        connector = OKXAPIConnector()
        connector._rl_min_interval = 0  # 加速
        for _ in range(5):
            connector._enforce_rate_limit()
        assert connector._rl_count_in_window == 5

    def test_window_reset_when_expired(self):
        import time

        connector = OKXAPIConnector()
        connector._rl_max_per_minute = 2
        connector._rl_count_in_window = 2
        connector._rl_window_start = time.time() - 61  # 窗口已過期

        connector._enforce_rate_limit()
        # 窗口過期 → 重置，計數歸 1
        assert connector._rl_count_in_window == 1


def _make_resp(status_code, headers=None):
    import requests

    resp = requests.Response()
    resp.status_code = status_code
    if headers:
        resp.headers.update(headers)
    resp._content = b'{"code":"0","data":[]}'
    return resp


class TestHTTPStatusHandling:
    """_make_request 的 429/418/5xx 處理（舊版完全沒有 status code 檢查）。"""

    def test_429_returns_rate_limited_code(self):
        connector = OKXAPIConnector()
        resp = _make_resp(429, headers={"Retry-After": "0.01"})
        with patch("utils.okx_api_connector.requests.get", return_value=resp):
            with patch.object(connector, "_enforce_rate_limit"):
                with patch("utils.okx_api_connector.time.sleep"):
                    result = connector._make_request(
                        "GET", "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"}
                    )
        assert result["code"] == "429"

    def test_429_reads_retry_after(self):
        connector = OKXAPIConnector()
        resp = _make_resp(429, headers={"Retry-After": "5"})
        sleep_calls = []
        with patch("utils.okx_api_connector.requests.get", return_value=resp):
            with patch.object(connector, "_enforce_rate_limit"):
                with patch(
                    "utils.okx_api_connector.time.sleep",
                    side_effect=lambda s: sleep_calls.append(s),
                ):
                    connector._make_request(
                        "GET", "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"}
                    )
        assert sleep_calls[0] == 5, f"應 sleep Retry-After=5s，實際 {sleep_calls[0]}"

    def test_418_aborts(self):
        connector = OKXAPIConnector()
        resp = _make_resp(418)
        with patch("utils.okx_api_connector.requests.get", return_value=resp):
            with patch.object(connector, "_enforce_rate_limit"):
                result = connector._make_request(
                    "GET", "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"}
                )
        assert result["code"] == "418"

    def test_5xx_returns_server_error(self):
        connector = OKXAPIConnector()
        resp = _make_resp(503)
        with patch("utils.okx_api_connector.requests.get", return_value=resp):
            with patch.object(connector, "_enforce_rate_limit"):
                result = connector._make_request(
                    "GET", "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"}
                )
        assert result["code"] == "503"

    def test_200_returns_json(self):
        """HTTP 200 → 正常回傳 JSON（不破壞既有行為）。"""
        connector = OKXAPIConnector()
        resp = _make_resp(200)
        with patch("utils.okx_api_connector.requests.get", return_value=resp):
            with patch.object(connector, "_enforce_rate_limit"):
                result = connector._make_request(
                    "GET", "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"}
                )
        assert result["code"] == "0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
