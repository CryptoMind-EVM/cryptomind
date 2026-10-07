import pytest


@pytest.mark.integration
class TestNegativePaths:
    @pytest.mark.asyncio
    async def test_analysis_modes_endpoint_is_removed(self, client):
        response = await client.get("/api/analyze/modes")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_removed_analysis_modes_endpoint_does_not_expose_mode_contract(
        self, client
    ):
        response = await client.get(
            "/api/analyze/modes",
            headers={"Authorization": "Bearer invalid-token-here"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_required_fields_returns_422(self, client, auth_headers):
        response = await client.post(
            "/api/forum/posts",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_nonexistent_route_returns_404(self, client, auth_headers):
        response = await client.get("/api/nonexistent-endpoint", headers=auth_headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_health_endpoint_always_accessible(self, client):
        response = await client.get("/health")
        assert response.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_post_to_get_only_endpoint(self, client):
        response = await client.post("/health")
        assert response.status_code == 405

    @pytest.mark.asyncio
    async def test_empty_authorization_does_not_restore_removed_modes_endpoint(
        self, client
    ):
        response = await client.get(
            "/api/analyze/modes",
            headers={"Authorization": ""},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_bearer_does_not_restore_removed_modes_endpoint(self, client):
        response = await client.get(
            "/api/analyze/modes",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 404
