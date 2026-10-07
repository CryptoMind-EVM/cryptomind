import pytest


@pytest.mark.integration
class TestAnalysisIdor:
    @pytest.mark.asyncio
    async def test_delete_session_requires_ownership(self, client, auth_headers):
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:
            mock_run.return_value = []
            response = await client.delete(
                "/api/chat/sessions/other-users-session-id",
                headers=auth_headers,
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_pin_session_requires_ownership(self, client, auth_headers):
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:
            mock_run.return_value = []
            response = await client.put(
                "/api/chat/sessions/other-users-session-id/pin?is_pinned=true",
                headers=auth_headers,
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_own_session_succeeds(self, client, auth_headers):
        from unittest.mock import patch

        session_id = "my-session-123"
        with patch("api.routers.analysis.run_sync") as mock_run:

            def fake_run(fn, *args):
                if callable(fn):
                    return [{"id": session_id}]
                return None

            mock_run.side_effect = fake_run
            response = await client.delete(
                f"/api/chat/sessions/{session_id}",
                headers=auth_headers,
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_pin_own_session_succeeds(self, client, auth_headers):
        from unittest.mock import patch

        session_id = "my-session-456"
        with patch("api.routers.analysis.run_sync") as mock_run:

            def fake_run(fn, *args):
                if callable(fn):
                    return [{"id": session_id}]
                return None

            mock_run.side_effect = fake_run
            response = await client.put(
                f"/api/chat/sessions/{session_id}/pin?is_pinned=true",
                headers=auth_headers,
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_pin_order_uses_authenticated_user_only(self, client, auth_headers):
        """pin-order 是寬鬆排序、不逐一檢查擁有權：靠 DB 層只動 user_id 自己的置頂
        （tests/test_chat_pins.py 用真 PG 驗別人的對話不受影響）。這裡守住 user_id 只從
        登入身分來、body 塞 user_id 沒用，而且路由沒被 /api/chat/sessions/{session_id} 吃掉。"""
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:
            mock_run.return_value = None
            response = await client.put(
                "/api/chat/sessions/pin-order",
                headers=auth_headers,
                json={"session_ids": ["other-users-session-id"], "user_id": "victim"},
            )
        assert response.status_code == 200
        fn, user_id, session_ids = mock_run.call_args.args
        assert fn.__name__ == "reorder_pinned_sessions"
        assert user_id == "test-user-001"
        assert session_ids == ["other-users-session-id"]
