"""
Tests for utility tools in core/tools/utility_tools.py
"""

from unittest.mock import patch

import pytest

from core.tools.utility_tools import get_current_time_tool, introduction_tool


class TestGetCurrentTimeTool:
    """Tests for get_current_time_tool function"""

    def test_default_timezone(self):
        """Test with default timezone (Asia/Taipei)"""
        result = get_current_time_tool.invoke({"timezone": "Asia/Taipei"})

        assert "Current Time" in result
        assert "Timezone" in result
        assert "Asia/Taipei" in result
        assert "UTC" in result

    def test_utc_timezone(self):
        """Test with UTC timezone"""
        result = get_current_time_tool.invoke({"timezone": "UTC"})

        assert "UTC" in result

    def test_new_york_timezone(self):
        """Test with America/New_York timezone"""
        result = get_current_time_tool.invoke({"timezone": "America/New_York"})

        assert "America/New_York" in result

    def test_tokyo_timezone(self):
        """Test with Asia/Tokyo timezone"""
        result = get_current_time_tool.invoke({"timezone": "Asia/Tokyo"})

        assert "Asia/Tokyo" in result

    def test_invalid_timezone_falls_back_to_taipei(self):
        """Test that invalid timezone falls back to Asia/Taipei"""
        result = get_current_time_tool.invoke({"timezone": "Invalid/Timezone"})

        # Should still return valid result with default timezone
        assert "Current Time" in result
        assert "Asia/Taipei" in result

    def test_output_format(self):
        """Test that output contains expected format elements"""
        result = get_current_time_tool.invoke({"timezone": "Asia/Taipei"})

        # Check for English weekday
        weekdays = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        has_weekday = any(day in result for day in weekdays)
        assert has_weekday

        # Check for period markers
        assert "AM" in result or "PM" in result

    def test_tool_has_correct_name(self):
        """Test that tool has correct name"""
        assert get_current_time_tool.name == "get_current_time_tool"

    def test_tool_has_description(self):
        """Test that tool has description"""
        assert get_current_time_tool.description
        assert "time" in get_current_time_tool.description.lower()

    def test_empty_timezone_uses_default(self):
        """Test that empty string timezone uses default"""
        result = get_current_time_tool.invoke({"timezone": ""})
        # Falls back to Taipei due to invalid timezone
        assert "Current Time" in result


class TestIntroductionTool:
    """Tests for introduction_tool function.

    Note: ``introduction_tool`` was refactored to return a hardcoded string
    (no longer reads from a file). Earlier tests mocked ``open()``, but the
    current implementation doesn't touch the filesystem, so those mocks were
    no-ops and the assertions were checking against stale fixtures. These
    tests now verify the actual behavior: the tool returns the platform /
    developer info string.
    """

    def test_tool_has_correct_name(self):
        """Test that tool has correct name"""
        assert introduction_tool.name == "introduction_tool"

    def test_tool_has_description(self):
        """Test that tool has description"""
        assert introduction_tool.description
        assert (
            "developer" in introduction_tool.description.lower()
            or "platform" in introduction_tool.description.lower()
        )

    def test_returns_platform_info_prefix(self):
        """Result should contain the standard prefix."""
        result = introduction_tool.invoke("")
        assert "Platform developer details" in result

    def test_returns_developer_info_content(self):
        """Result should contain CryptoMind developer info."""
        result = introduction_tool.invoke("")
        assert "CryptoMind" in result
        assert "GitHub" in result

    def test_does_not_read_filesystem(self):
        """Sanity: tool should NOT touch the filesystem.

        Regression guard: if someone reintroduces file reading, this test
        will fail (because patching open would intercept it).
        """
        with patch("builtins.open", side_effect=AssertionError("should not open")):
            result = introduction_tool.invoke("")
        assert "CryptoMind" in result

    def test_handles_any_query_arg(self):
        """Tool ignores query arg and always returns the same info."""
        r1 = introduction_tool.invoke("")
        r2 = introduction_tool.invoke("anything")
        assert r1 == r2


class TestTimeFormatting:
    """Tests for time formatting logic"""

    def test_weekday_mapping(self):
        """Test that all weekdays are properly mapped to English"""
        # This tests the weekday_map inside the function indirectly
        result = get_current_time_tool.invoke({"timezone": "Asia/Taipei"})

        # Check that one of the English weekdays is in the result
        weekdays = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        found_weekday = any(day in result for day in weekdays)
        assert found_weekday

    def test_12_hour_format(self):
        """Test that 12-hour format is included"""
        result = get_current_time_tool.invoke({"timezone": "Asia/Taipei"})

        # Should contain AM or PM
        assert "AM" in result or "PM" in result

    def test_utc_time_included(self):
        """Test that UTC time is included in output"""
        result = get_current_time_tool.invoke({"timezone": "Asia/Taipei"})

        assert "UTC Time" in result
        # UTC time should be in ISO-like format
        assert "-" in result  # Date separator
        assert ":" in result  # Time separator


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
