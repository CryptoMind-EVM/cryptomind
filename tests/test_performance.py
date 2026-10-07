"""Tests for Phase 3 performance improvements."""

import inspect

import pytest


class TestPaginationAdded:
    """Verify pagination parameters on list endpoints."""

    @pytest.mark.parametrize(
        "func_name",
        [
            "list_comments",
        ],
    )
    def test_comments_has_limit_offset(self, func_name):
        import api.routers.forum.comments as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "limit" in params, f"{func_name} missing 'limit' param"
        assert "offset" in params, f"{func_name} missing 'offset' param"

    @pytest.mark.parametrize(
        "func_name",
        [
            "get_user_sessions",
        ],
    )
    def test_sessions_has_limit_offset(self, func_name):
        import api.routers.analysis as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "limit" in params, f"{func_name} missing 'limit' param"
        assert "offset" in params, f"{func_name} missing 'offset' param"

    @pytest.mark.parametrize(
        "func_name",
        [
            "get_my_sent_tips",
            "get_my_received_tips",
            "get_my_payments",
        ],
    )
    def test_me_endpoints_have_offset(self, func_name):
        import api.routers.forum.me as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "offset" in params, f"{func_name} missing 'offset' param"

    @pytest.mark.parametrize(
        "func_name",
        [
            "get_sent_tips",
            "get_received_tips",
        ],
    )
    def test_tips_endpoints_have_offset(self, func_name):
        import api.routers.forum.tips as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "offset" in params, f"{func_name} missing 'offset' param"

    @pytest.mark.parametrize(
        "func_name",
        [
            "list_my_reports",
            "get_my_activity_logs",
        ],
    )
    def test_governance_endpoints_have_offset(self, func_name):
        import api.routers.governance as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "offset" in params, f"{func_name} missing 'offset' param"

    @pytest.mark.parametrize(
        "func_name",
        [
            "get_blocked_list",
            "get_received_requests",
            "get_sent_requests",
        ],
    )
    def test_friends_endpoints_have_limit(self, func_name):
        import api.routers.friends as mod

        func = getattr(mod, func_name, None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert "limit" in params, f"{func_name} missing 'limit' param"


class TestDbLayerPagination:
    """Verify DB layer functions accept limit/offset."""

    def test_get_comments_accepts_limit_offset(self):
        import core.database.forum as mod

        sig = inspect.signature(mod.get_comments)
        params = list(sig.parameters.keys())
        assert "limit" in params
        assert "offset" in params

    def test_get_comments_has_limit_offset_sql(self):
        import core.database.forum as mod

        src = inspect.getsource(mod.get_comments)
        assert "LIMIT" in src
        assert "OFFSET" in src

    def test_get_tips_sent_accepts_offset(self):
        import core.database.forum as mod

        sig = inspect.signature(mod.get_tips_sent)
        assert "offset" in sig.parameters

    def test_get_tips_received_accepts_offset(self):
        import core.database.forum as mod

        sig = inspect.signature(mod.get_tips_received)
        assert "offset" in sig.parameters

    def test_get_user_payment_history_accepts_offset(self):
        import core.database.forum as mod

        sig = inspect.signature(mod.get_user_payment_history)
        assert "offset" in sig.parameters

    def test_get_sessions_accepts_offset(self):
        import core.database.chat as mod

        sig = inspect.signature(mod.get_sessions)
        assert "offset" in sig.parameters

    def test_get_user_reports_accepts_offset(self):
        import core.database.governance.reports as mod

        sig = inspect.signature(mod.get_user_reports)
        assert "offset" in sig.parameters

    def test_get_blocked_users_accepts_limit(self):
        # #954 起封鎖清單只剩 ORM 版（core/database/friends.py 的 raw 版已刪）
        from core.orm.friends_repo import FriendsRepository

        sig = inspect.signature(FriendsRepository.get_blocked_users)
        assert "limit" in sig.parameters

    def test_get_pending_requests_received_accepts_limit(self):
        import core.database.friends as mod

        sig = inspect.signature(mod.get_pending_requests_received)
        assert "limit" in sig.parameters

    def test_get_pending_requests_sent_accepts_limit(self):
        import core.database.friends as mod

        sig = inspect.signature(mod.get_pending_requests_sent)
        assert "limit" in sig.parameters


class TestPoolTuning:
    """Verify connection pool is configurable via environment variables."""

    def test_pool_size_uses_env_vars(self):
        import core.database.connection as mod

        src = inspect.getsource(mod)
        assert "DB_MIN_POOL_SIZE" in src
        assert "DB_MAX_POOL_SIZE" in src

    def test_connect_timeout_uses_env_var(self):
        import core.database.connection as mod

        src = inspect.getsource(mod)
        assert "DB_CONNECT_TIMEOUT" in src

    def test_statement_timeout_uses_env_var(self):
        import core.database.connection as mod

        src = inspect.getsource(mod)
        assert "DB_STATEMENT_TIMEOUT" in src
        assert "statement_timeout" in src

    def test_async_engine_has_statement_timeout(self):
        """async engine 也要設 statement_timeout（2026-08-12 /me timeout 事件）。"""
        from core.orm import session as mod

        src = inspect.getsource(mod)
        assert "statement_timeout" in src
        assert "server_settings" in src


class TestStockDataCacheTTL:
    """日頻資料快取 TTL 依業界慣例分級（2026-08 stock data cache 統一）。

    業界依據：news feeds 1-15min（Imperva/Finnhub/FMP）、fundamentals 數天-數週
    （Intrinio，季頻）、daily 資料 6h。
    """

    def test_fundamentals_ttl_is_6h(self):
        from core.providers import yahoo_provider as mod

        assert "ttl=21600" in inspect.getsource(mod)

    def test_twse_daily_data_ttl_is_6h(self):
        from core.providers import twse_provider as mod

        # PE(detail) + PE(fetch) + dividend + monthly_revenue + extras + institutional
        assert inspect.getsource(mod).count("ttl=21600") >= 5

    def test_news_cache_ttl_is_5min(self):
        from core.tools import tw_stock_tools as mod

        src = inspect.getsource(mod)
        assert "ttl=300" in src  # _NEWS_CACHE（news 5min）
        assert "ttl=21600" in src  # _FOREIGN_HOLDING_CACHE（外資日頻 6h）

    def test_yf_helpers_news_delegates_to_provider(self):
        from api.routers import yf_helpers as mod

        # fetch_news_sync 改走已快取的 provider.get_news，不再自己抓 yfinance
        assert "_shared_yf_provider().get_news" in inspect.getsource(mod)

    def test_pool_defaults_are_sensible(self):
        import core.database.connection as mod

        assert 1 <= mod.MIN_POOL_SIZE <= 10
        assert 5 <= mod.MAX_POOL_SIZE <= 100
        assert mod.MIN_POOL_SIZE <= mod.MAX_POOL_SIZE
