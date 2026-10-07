"""
Tests for validators module
"""

import pytest

from core.validators import mask_wallet_address
from core.validators.content_filter import (
    filter_sensitive_content,
    sanitize_description,
)


class TestWalletAddressMasking:
    """Tests for wallet address masking helper"""

    def test_address_masking(self):
        """Test wallet address masking"""
        address = "UQDvjDhEZ128EktbSBrK4CWrw1xTTbx4ojlZ1234567890ABCDEF"
        masked = mask_wallet_address(address, mask_length=4)
        assert masked == "UQDv...CDEF"

    def test_address_masking_short(self):
        """Test masking short address (should return as-is)"""
        address = "UQAB"
        masked = mask_wallet_address(address, mask_length=4)
        assert masked == address


class TestContentFilter:
    """Tests for content filter"""

    def test_valid_content(self):
        """Test valid content"""
        content = "這是一個正常的詐騙描述，該地址假冒官方進行詐騙，請大家小心"
        result = filter_sensitive_content(content)
        assert result["valid"] is True
        assert len(result["warnings"]) == 0

    def test_empty_content(self):
        """Test empty content"""
        result = filter_sensitive_content("")
        assert result["valid"] is False
        assert any("不能為空" in w for w in result["warnings"])

    def test_content_too_short(self):
        """Test content that is too short"""
        result = filter_sensitive_content("太短了")
        assert result["valid"] is False
        assert any("過短" in w for w in result["warnings"])

    def test_content_too_long(self):
        """Test content that is too long"""
        long_content = "a" * 2001
        result = filter_sensitive_content(long_content)
        assert result["valid"] is False
        assert any("過長" in w for w in result["warnings"])

    def test_content_with_email(self):
        """Test content containing email address"""
        result = filter_sensitive_content(
            "這是詐騙請聯絡我 scam@example.com " + "x" * 50
        )
        assert result["valid"] is False
        assert any("郵件" in w for w in result["warnings"])

    def test_content_with_phone(self):
        """Test content containing phone number"""
        result = filter_sensitive_content("這是詐騙電話 0912345678 " + "x" * 50)
        assert result["valid"] is False
        assert any("電話" in w for w in result["warnings"])

    def test_content_with_url(self):
        """Test content containing non-official URL"""
        result = filter_sensitive_content("這是詐騙請訪問 https://scam.com " + "x" * 50)
        assert result["valid"] is False
        assert any("網址" in w for w in result["warnings"])

    def test_content_with_sensitive_words(self):
        """Test content containing sensitive words"""
        result = filter_sensitive_content("這是詐騙請加我微信詳談 " + "x" * 50)
        assert result["valid"] is False
        assert any("敏感詞" in w or "微信" in w for w in result["warnings"])

    def test_sanitize_description(self):
        """Test description sanitization"""
        dirty = "  這是   多餘空白\n\n的描述  "
        clean = sanitize_description(dirty)
        assert clean == "這是 多餘空白 的描述"

    def test_sanitize_empty(self):
        """Test sanitizing empty content"""
        clean = sanitize_description("")
        assert clean == ""

    def test_sanitize_none(self):
        """Test sanitizing None"""
        clean = sanitize_description(None)
        assert clean == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
