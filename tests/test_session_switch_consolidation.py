"""換對話時的記憶整合觸發測試。

覆蓋兩條接線路徑：
1. POST /api/chat/current-session（切換既有對話）→ _trigger_session_consolidation
2. POST /api/chat/sessions（新對話第一則訊息的 lazy creation）→ _trigger_session_consolidation

以及 _trigger_session_consolidation helper 本身的行為：
- _message_count <= 0 時早退
- 正常時 fire-and-forget 觸發 _background_memory_consolidation
- consolidate 完成（或失敗）後 invalidate_manager_cache 被呼叫

對齊 tests/test_chat_session_create.py 的 mock 風格：
patch("api.routers.analysis.run_sync") + side_effect 依序模擬多次 run_sync 呼叫。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

USER_ID = "test-user-001"


def _run_sync_side_effect(callables_to_values):
    """造一個 run_sync side_effect，依 callable 類型回不同值。

    endpoint 裡 run_sync 被呼叫的 fn 有：
    - check_session_ownership（回 list/bool）
    - get_current_session（回 str 或 None）
    - set_current_session（回 None）
    - create_session（包在 lambda 裡，回 None）
    透過 fn 是否在 mapping 裡判斷回傳值，其餘回 None。
    """
    mapping = dict(callables_to_values)

    async def _side_effect(fn, *args, **kwargs):
        if fn in mapping:
            return mapping[fn]
        # lambda（create_session wrapper）/ set_current_session 等無回傳值的 fn：
        # 直接回 None，不執行 fn（避免碰到真實 DB）。
        return None

    return _side_effect


# ============================================================================
# Endpoint 接線測試：current-session（切換既有對話）
# ============================================================================


@pytest.mark.integration
class TestCurrentSessionConsolidation:
    @pytest.mark.asyncio
    async def test_triggers_consolidation_for_old_session(self, client, auth_headers):
        """切換時 old_session_id 存在且不同 → 呼叫 _trigger_session_consolidation。"""
        from core.database import (
            check_session_ownership,
            get_current_session,
            set_current_session,
        )

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {
                        check_session_ownership: True,
                        get_current_session: "old-sess-123",
                        set_current_session: None,
                    }
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/current-session",
                json={"session_id": "new-sess-456"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_trigger.assert_called_once_with(USER_ID, "old-sess-123")

    @pytest.mark.asyncio
    async def test_skips_when_no_old_session(self, client, auth_headers):
        """old_session_id 為 None（首次切換）→ 不觸發。"""
        from core.database import check_session_ownership, get_current_session

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {
                        check_session_ownership: True,
                        get_current_session: None,
                    }
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/current-session",
                json={"session_id": "new-sess-456"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_trigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_same_session(self, client, auth_headers):
        """old == new（重複寫同一個 session）→ 不觸發。"""
        from core.database import check_session_ownership, get_current_session

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {
                        check_session_ownership: True,
                        get_current_session: "same-sess",
                    }
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/current-session",
                json={"session_id": "same-sess"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_trigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_403_does_not_trigger_consolidation(self, client, auth_headers):
        """ownership check 失敗 → 403，不該觸發 consolidate。"""
        from core.database import check_session_ownership

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {check_session_ownership: False}
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/current-session",
                json={"session_id": "not-yours"},
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_trigger.assert_not_called()


# ============================================================================
# Endpoint 接線測試：sessions（新對話 lazy creation）
# ============================================================================


@pytest.mark.integration
class TestCreateSessionConsolidation:
    @pytest.mark.asyncio
    async def test_triggers_consolidation_for_old_session(self, client, auth_headers):
        """新對話 sessions endpoint：old_session_id 存在 → 觸發整合。"""
        from core.database import get_current_session

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {
                        get_current_session: "old-sess-789",
                    }
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/sessions",
                json={"title": "new chat"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        new_session_id = body["session_id"]
        mock_trigger.assert_called_once_with(USER_ID, "old-sess-789")
        # 新 session id 不該等於舊的
        assert new_session_id != "old-sess-789"

    @pytest.mark.asyncio
    async def test_skips_when_no_old_session(self, client, auth_headers):
        """old_session_id 為 None（首次建立對話）→ 不觸發。"""
        from core.database import get_current_session

        with (
            patch(
                "api.routers.analysis.run_sync",
                side_effect=_run_sync_side_effect(
                    {get_current_session: None}
                ),
            ),
            patch("api.routers.analysis._trigger_session_consolidation") as mock_trigger,
        ):
            response = await client.post(
                "/api/chat/sessions",
                json={"title": "first chat"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_trigger.assert_not_called()


# ============================================================================
# _trigger_session_consolidation helper 內部行為測試
# ============================================================================


class TestTriggerSessionConsolidationHelper:
    def test_no_manager_does_nothing(self):
        """old session 沒有 manager 實例（cache miss）→ 不觸發任何事。"""
        from api.routers.analysis import _trigger_session_consolidation

        with (
            patch(
                "core.agents.bootstrap.get_manager_instance", return_value=None
            ),
            patch("core.agents.manager._main._run_background") as mock_bg,
        ):
            _trigger_session_consolidation("u1", "no-such-session")

        mock_bg.assert_not_called()

    def test_manager_with_zero_messages_does_nothing(self):
        """manager 存在但 _message_count=0（沒新訊息）→ 不白跑 consolidate。"""
        from api.routers.analysis import _trigger_session_consolidation

        fake_manager = MagicMock()
        fake_manager._message_count = 0
        with (
            patch(
                "core.agents.bootstrap.get_manager_instance",
                return_value=fake_manager,
            ),
            patch("core.agents.manager._main._run_background") as mock_bg,
        ):
            _trigger_session_consolidation("u1", "sess-empty")

        mock_bg.assert_not_called()

    def test_triggers_background_consolidation_and_invalidate(self):
        """正常情境：有 manager + 有訊息 → _run_background 被呼叫一次。"""
        from api.routers.analysis import _trigger_session_consolidation

        fake_manager = MagicMock()
        fake_manager._message_count = 5
        fake_manager._background_memory_consolidation = AsyncMock()
        with (
            patch(
                "core.agents.bootstrap.get_manager_instance",
                return_value=fake_manager,
            ),
            patch("core.agents.manager._main._run_background") as mock_bg,
            patch("core.agents.bootstrap.invalidate_manager_cache") as mock_invalidate,
        ):
            _trigger_session_consolidation("u1", "sess-old")

        mock_bg.assert_called_once()
        # _run_background 收到的是 coroutine；提取它驗證內部會跑 consolidate
        coro = mock_bg.call_args[0][0]
        # 必須是 coroutine 物件（fire-and-forget 不 await）
        import asyncio

        assert asyncio.iscoroutine(coro)
        # 跑它一次，確認 consolidate 被 await、invalidate 被 call
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
        fake_manager._background_memory_consolidation.assert_awaited_once()
        mock_invalidate.assert_called_once_with("u1", "sess-old")

    def test_invalidate_called_even_if_consolidation_fails(self):
        """consolidate 拋例外時，finally 仍要 invalidate（避免殭屍 manager）。"""
        from api.routers.analysis import _trigger_session_consolidation

        fake_manager = MagicMock()
        fake_manager._message_count = 5
        fake_manager._background_memory_consolidation = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )
        with (
            patch(
                "core.agents.bootstrap.get_manager_instance",
                return_value=fake_manager,
            ),
            patch("core.agents.manager._main._run_background") as mock_bg,
            patch("core.agents.bootstrap.invalidate_manager_cache") as mock_invalidate,
        ):
            _trigger_session_consolidation("u1", "sess-old")

        coro = mock_bg.call_args[0][0]
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            # 內部拋例外要被 re-raise（讓 _run_background 的 _on_done 記錄）
            with pytest.raises(RuntimeError, match="LLM down"):
                loop.run_until_complete(coro)
        finally:
            loop.close()
        # finally 仍執行 invalidate
        mock_invalidate.assert_called_once_with("u1", "sess-old")
