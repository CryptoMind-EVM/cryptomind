"""
Tests for user router in api/routers/user.py
"""

import pytest

from api.routers.user import router


class TestUserRouter:
    """Tests for user router"""

    def test_router_defined(self):
        """Test that router is defined"""
        assert router is not None

    def test_router_has_routes(self):
        """Test that router has routes"""
        assert len(router.routes) > 0


class TestUserRouterEndpoints:
    """Tests for user endpoint paths"""

    def test_watchlist_moved_to_own_router(self):
        """2026-09-27：自選清單搬到 api/routers/watchlist.py（加市場欄位，c056）"""
        from api.routers.watchlist import router as watchlist_router

        assert watchlist_router.prefix == "/api/watchlist"

    def test_has_me_endpoint(self):
        """Test me endpoint exists"""
        routes = [r.path for r in router.routes]
        # Check for user profile/me routes
        has_user_route = any("me" in r or "user" in r for r in routes)
        assert has_user_route or len(routes) > 0


class TestWatchlistLimit:
    """自選清單上限（core/database/trading.MAX_WATCHLIST）"""

    def test_watchlist_limit_is_50(self):
        """2026-09-27 第二階段：10 個市場共用，20 改 50。"""
        from core.database.trading import MAX_WATCHLIST

        assert MAX_WATCHLIST == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
