"""Open Collective client 測試 — core/tools/opencollective_client.py.

實際 schema 已於 2026-08-17 對線上 API 驗證；此處以同形狀的假回應測
解析與三種降級（網路錯誤 / 429 / graphql errors）。
"""

from __future__ import annotations

import pytest

from core.tools.opencollective_client import (
    OCUnavailableError,
    search_collectives,
)

_GOOD_BODY = {
    "data": {
        "accounts": {
            "nodes": [
                {
                    "slug": "venten",
                    "name": "Venten",
                    "type": "COLLECTIVE",
                    "description": "AI Safety Technical Research for Latam",
                    "website": "https://venten.ai/",
                    "tags": ["ai safety", "research"],
                    "stats": {"totalAmountReceived": {"value": 120, "currency": "USD"}},
                },
                {
                    "slug": "someone",
                    "name": "Someone Personal",
                    "type": "INDIVIDUAL",
                    "description": None,
                    "website": None,
                    "tags": None,
                    "stats": {"totalAmountReceived": {"value": 0, "currency": "USD"}},
                },
            ]
        }
    }
}


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return self._response


@pytest.mark.asyncio
class TestSearchCollectives:
    async def test_normalizes_collectives_and_skips_individuals(self):
        import httpx

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _FakeClient(response=_FakeResponse(200, _GOOD_BODY)),
            )
            results = await search_collectives("ai safety", limit=5)
        assert len(results) == 1
        r = results[0]
        assert r["id"] == "venten"
        assert r["source"] == "oc"
        assert r["item_type"] == "oc_collective"
        assert r["url"] == "https://opencollective.com/venten"
        assert r["total_raised"] == 120

    async def test_http_error_raises_unavailable(self):
        import httpx

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _FakeClient(exc=httpx.ConnectError("boom")),
            )
            with pytest.raises(OCUnavailableError):
                await search_collectives("x")

    async def test_rate_limited_raises_unavailable(self):
        import httpx

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _FakeClient(response=_FakeResponse(429, {})),
            )
            with pytest.raises(OCUnavailableError):
                await search_collectives("x")

    async def test_graphql_errors_raise_unavailable(self):
        """schema 變更 → 明確降級，不把半套資料當成功。"""
        import httpx

        body = {"errors": [{"message": "Unknown argument"}]}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _FakeClient(response=_FakeResponse(200, body)),
            )
            with pytest.raises(OCUnavailableError):
                await search_collectives("x")
