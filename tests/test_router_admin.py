"""Tests for the current modular admin router package."""


import pytest

from api.routers.admin import UpdateConfigRequest, router


def _route_paths(target_router) -> list[str]:
    """Flatten full route paths; handles starlette>=1.x lazy router includes."""
    paths = []
    stack = [("", route) for route in target_router.routes]
    while stack:
        prefix, route = stack.pop()
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(prefix + path)
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            inner_prefix = prefix + (getattr(context, "prefix", "") or "")
            stack.extend((inner_prefix, inner) for inner in original.routes)
    return paths


class TestRequestModels:
    """Tests for exposed request models."""

    def test_update_config_request_valid(self):
        req = UpdateConfigRequest(value="new_value")
        assert req.value == "new_value"

    def test_update_config_request_missing_fields(self):
        with pytest.raises(Exception):
            UpdateConfigRequest()


class TestAdminRouterRoutes:
    """Tests for current route definitions."""

    def test_router_defined(self):
        assert router is not None

    def test_router_has_routes(self):
        assert len(router.routes) > 0

    def test_has_config_all_route(self):
        routes = _route_paths(router)
        assert "/api/admin/config/all" in routes

    def test_has_config_update_route(self):
        routes = _route_paths(router)
        assert "/api/admin/config/{key}" in routes

    def test_has_config_audit_route(self):
        routes = _route_paths(router)
        assert "/api/admin/config/audit" in routes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
