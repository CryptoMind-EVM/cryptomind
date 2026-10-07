"""
Tests for API request/response models in api/models.py
"""

import pytest
from pydantic import ValidationError

from api.models import (
    KeyValidationRequest,
    KlineRequest,
    QueryRequest,
    RefreshPulseRequest,
    ScreenerRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserSettings,
    _dedup_doubled_message,
)


class TestQueryRequest:
    """Tests for QueryRequest model"""

    def test_required_fields(self):
        """Test that required fields are enforced"""
        data = {
            "message": "Test message",
            "user_provider": "openai",
        }
        request = QueryRequest(**data)
        assert request.message == "Test message"
        assert request.user_provider == "openai"

    def test_default_values(self):
        """Test default values"""
        request = QueryRequest(message="Test", user_provider="google_gemini")
        assert request.interval == "1d"  # DEFAULT_INTERVAL
        assert request.auto_execute is False
        assert request.market_type == "spot"
        assert request.session_id == "default"

    def test_custom_values(self):
        """Test with custom values"""
        data = {
            "message": "Test",
            "interval": "4h",
            "limit": 200,
            "manual_selection": ["analyst1", "analyst2"],
            "auto_execute": True,
            "market_type": "futures",
            "user_provider": "openai",
            "user_model": "gpt-4",
            "session_id": "custom-session",
        }
        request = QueryRequest(**data)
        assert request.interval == "4h"
        assert request.limit == 200
        assert request.auto_execute is True
        assert request.market_type == "futures"

    def test_missing_required_fields(self):
        """Test that missing required fields raise ValidationError"""
        with pytest.raises(ValidationError):
            QueryRequest()  # Missing message

    def test_message_max_length_enforced(self):
        """message 超過 4000 字應在 Pydantic 層被擋(缺口 B)。"""
        with pytest.raises(ValidationError):
            QueryRequest(message="x" * 4001)

    def test_message_at_max_length_accepted(self):
        """message 剛好 4000 字應接受(邊界值)。

        不能用重複同一字元（如 'x'*4000）——入口的 _dedup_doubled_message
        會把「前後半完全相同」的訊息去重成 2000 字（那是防前端拼接 bug 的
        防禦邏輯，不是長度上限）。用前後半不同的 4000 字串測真實邊界。
        """
        message = ("a" * 2000) + ("b" * 2000)
        request = QueryRequest(message=message)
        assert len(request.message) == 4000

    def test_system_prompt_max_length_enforced(self):
        """system_prompt 超過 2000 字應在 Pydantic 層被擋(缺口 C)。"""
        with pytest.raises(ValidationError):
            QueryRequest(message="ok", system_prompt="x" * 2001)

    def test_system_prompt_at_max_length_accepted(self):
        """system_prompt 剛好 2000 字應接受。"""
        request = QueryRequest(message="ok", system_prompt="x" * 2000)
        assert len(request.system_prompt) == 2000

    def test_system_prompt_none_accepted(self):
        """system_prompt=None(預設)應接受。"""
        request = QueryRequest(message="ok")
        assert request.system_prompt is None


class TestScreenerRequest:
    """Tests for ScreenerRequest model"""

    def test_default_values(self):
        """Test default values"""
        request = ScreenerRequest()
        assert request.exchange == "okx"  # SUPPORTED_EXCHANGES[0]
        assert request.symbols is None
        assert request.refresh is False

    def test_custom_values(self):
        """Test with custom values"""
        request = ScreenerRequest(
            exchange="binance", symbols=["BTC", "ETH"], refresh=True
        )
        assert request.exchange == "binance"
        assert request.symbols == ["BTC", "ETH"]
        assert request.refresh is True


class TestUserRegisterRequest:
    """Tests for UserRegisterRequest model"""

    def test_required_fields(self):
        """Test required fields"""
        request = UserRegisterRequest(
            username="testuser", password="testpass"
        )  # pragma: allowlist secret
        assert request.username == "testuser"
        assert request.password == "testpass"  # pragma: allowlist secret

    def test_missing_fields(self):
        """Test that missing fields raise ValidationError"""
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="testuser")


class TestUserLoginRequest:
    """Tests for UserLoginRequest model"""

    def test_required_fields(self):
        """Test required fields"""
        request = UserLoginRequest(
            username="testuser", password="testpass"
        )  # pragma: allowlist secret
        assert request.username == "testuser"
        assert request.password == "testpass"  # pragma: allowlist secret


class TestKlineRequest:
    """Tests for KlineRequest model"""

    def test_required_symbol(self):
        """Test that symbol is required"""
        request = KlineRequest(symbol="BTC")
        assert request.symbol == "BTC"

    def test_default_values(self):
        """Test default values"""
        request = KlineRequest(symbol="ETH")
        assert request.exchange == "okx"
        assert request.interval == "1d"
        assert request.limit == 100

    def test_custom_values(self):
        """Test with custom values"""
        request = KlineRequest(
            symbol="SOL", exchange="binance", interval="4h", limit=200
        )
        assert request.exchange == "binance"
        assert request.interval == "4h"
        assert request.limit == 200


class TestUserSettings:
    """Tests for UserSettings model"""

    def test_default_values(self):
        """Test default values"""
        settings = UserSettings()
        assert settings.openai_api_key is None
        assert settings.google_api_key is None
        assert settings.primary_model_provider == "google_gemini"

    def test_custom_values(self):
        """Test with custom values"""
        settings = UserSettings(
            openai_api_key="sk-test",  # pragma: allowlist secret
            primary_model_provider="openai",
            primary_model_name="gpt-4",
        )
        assert settings.openai_api_key == "sk-test"  # pragma: allowlist secret
        assert settings.primary_model_provider == "openai"


class TestRefreshPulseRequest:
    """Tests for RefreshPulseRequest model"""

    def test_default_symbols_none(self):
        """Test default symbols is None"""
        request = RefreshPulseRequest()
        assert request.symbols is None

    def test_custom_symbols(self):
        """Test with custom symbols"""
        request = RefreshPulseRequest(symbols=["BTC", "ETH", "SOL"])
        assert request.symbols == ["BTC", "ETH", "SOL"]

    def test_empty_symbols_list(self):
        """Test with empty symbols list"""
        request = RefreshPulseRequest(symbols=[])
        assert request.symbols == []


class TestKeyValidationRequest:
    """Tests for KeyValidationRequest model"""

    def test_required_fields(self):
        """Test required fields"""
        request = KeyValidationRequest(
            provider="openai",
            api_key="sk-test-key",  # pragma: allowlist secret
        )
        assert request.provider == "openai"
        assert request.api_key == "sk-test-key"  # pragma: allowlist secret
        assert request.model is None

    def test_with_model(self):
        """Test with model specified"""
        request = KeyValidationRequest(
            provider="google_gemini", api_key="gemini-key", model="gemini-pro"
        )
        assert request.model == "gemini-pro"

    def test_various_providers(self):
        """Test various provider values"""
        for provider in ["openai", "google_gemini", "openrouter"]:
            request = KeyValidationRequest(provider=provider, api_key="test-key")
            assert request.provider == provider


class TestModelValidation:
    """Tests for model validation edge cases"""

    def test_query_request_with_empty_message(self):
        """Test QueryRequest with empty message"""
        request = QueryRequest(message="", user_provider="openai")
        assert request.message == ""

    def test_kline_request_with_zero_limit(self):
        """Test KlineRequest with zero limit"""
        request = KlineRequest(symbol="BTC", limit=0)
        assert request.limit == 0

    def test_user_settings_with_all_none(self):
        """Test UserSettings with all optional fields None"""
        settings = UserSettings()
        assert settings.openai_api_key is None
        assert settings.google_api_key is None
        assert settings.openrouter_api_key is None


class TestDedupDoubledMessage:
    """去重「完整訊息重複兩次」的 query（線上 TMA 偶發的 input 拼接 bug）。"""

    def test_real_case_dedup(self):
        """線上實際案例：完整句子重複兩次。"""
        dup = "幫我查用 100 TON 換 USDt 能拿到多少幫我查用 100 TON 換 USDt 能拿到多少"
        result = _dedup_doubled_message(dup)
        assert result == "幫我查用 100 TON 換 USDt 能拿到多少"

    def test_normal_message_untouched(self):
        """正常訊息不該被誤傷。"""
        normal = "幫我查用 100 TON 換 USDt 能拿到多少"
        assert _dedup_doubled_message(normal) == normal

    def test_odd_length_untouched(self):
        """奇數長度（不可能對稱）不處理。"""
        assert _dedup_doubled_message("幫我分析比特幣") == "幫我分析比特幣"

    def test_short_message_untouched(self):
        """短訊息（< 8 字元）不處理，避免誤傷重複詞（如 hi hi）。"""
        assert _dedup_doubled_message("hello") == "hello"
        assert _dedup_doubled_message("TON TON") == "TON TON"

    def test_empty_string(self):
        assert _dedup_doubled_message("") == ""

    def test_numbers_dedup(self):
        """數字重複也去重（通常是 bug）。"""
        assert _dedup_doubled_message("12341234") == "1234"

    def test_pydantic_validator_applied(self):
        """QueryRequest 的 Pydantic validator 自動去重。"""
        half = "swap 100 TON to USDt"  # 偶數長度，確保前後半可比對
        dup = half + half
        q = QueryRequest(message=dup, session_id="x")
        assert q.message == half


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
