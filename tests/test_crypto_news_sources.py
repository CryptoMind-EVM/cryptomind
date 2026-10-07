"""Tests for crypto news source selection defaults.

Behavior reference (utils/utils.py:get_crypto_news):
- ``cryptocompare`` is in ``BYOK_SOURCES`` but is treated as free in the docstring.
  The current product decision: when the user **explicitly** passes
  ``enabled_sources=["cryptocompare", ...]``, the function respects it and
  **does** call cryptocompare. Only the default path (no enabled_sources)
  restricts to FREE_SOURCES (just google).

These tests assert the actual behavior. The previous version of
``test_crypto_news_filters_premium_sources_from_explicit_requests`` expected
explicit requests to be filtered, which contradicted the implementation
(cryptocompare was always called when listed in enabled_sources).
"""

from unittest.mock import patch

from utils.utils import get_crypto_news


def test_crypto_news_defaults_to_google_rss_only():
    """Default aggregation (no enabled_sources) should only use the free RSS source."""
    with patch("utils.utils.get_crypto_news_google", return_value=[]):
        with patch("utils.utils.get_crypto_news_cryptocompare") as mock_compare:
            with patch("utils.utils.get_crypto_news_cryptopanic") as mock_panic:
                with patch("utils.utils.get_crypto_news_newsapi") as mock_newsapi:
                    with patch(
                        "core.tools.key_resolver.has_tool_key", return_value=False
                    ):
                        get_crypto_news("BTC", limit=5)

    mock_compare.assert_not_called()
    mock_panic.assert_not_called()
    mock_newsapi.assert_not_called()


def test_crypto_news_explicit_sources_are_respected():
    """Explicit enabled_sources should be respected — cryptocompare is called
    when listed, because it's a free API (no key required).

    This replaces the previous test that expected explicit premium requests
    to be filtered, which contradicted the implementation.
    """
    with patch("utils.utils.get_crypto_news_google", return_value=[]):
        with patch(
            "utils.utils.get_crypto_news_cryptocompare", return_value=[]
        ) as mock_compare:
            with patch("utils.utils.get_crypto_news_cryptopanic") as mock_panic:
                with patch("utils.utils.get_crypto_news_newsapi") as mock_newsapi:
                    with patch(
                        "core.tools.key_resolver.has_tool_key", return_value=False
                    ):
                        get_crypto_news(
                            "BTC",
                            limit=5,
                            enabled_sources=["google", "cryptocompare"],
                        )

    # cryptocompare IS called because the user explicitly listed it
    mock_compare.assert_called_once()
    # cryptopanic / newsapi are NOT called (not in enabled_sources, no BYOK key)
    mock_panic.assert_not_called()
    mock_newsapi.assert_not_called()


def test_crypto_news_byok_unlocked_sources_are_added():
    """When user has set BYOK key for a premium source, it gets added even
    if not explicitly listed in enabled_sources."""
    with patch("utils.utils.get_crypto_news_google", return_value=[]):
        with patch("utils.utils.get_crypto_news_cryptocompare") as mock_compare:
            with patch(
                "utils.utils.get_crypto_news_cryptopanic", return_value=[]
            ) as mock_panic:
                with patch("utils.utils.get_crypto_news_newsapi") as mock_newsapi:
                    # Simulate user having cryptopanic key unlocked
                    with patch(
                        "core.tools.key_resolver.has_tool_key",
                        side_effect=lambda s: s == "cryptopanic",
                    ):
                        get_crypto_news("BTC", limit=5)

    # cryptopanic is called because BYOK key unlocked it
    mock_panic.assert_called_once()
    # others are not
    mock_compare.assert_not_called()
    mock_newsapi.assert_not_called()
