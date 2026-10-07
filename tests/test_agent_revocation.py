"""
Tests for agent authorization revocation (Trustworthy AI hackathon element #6).

Covers POST /api/analyze/{run_id}/revoke:
  - Successful revoke cancels the underlying invoke_task
  - Ownership check rejects other users' runs (404, no leak)
  - Already-finished runs return 409
  - Audit log is written
"""

from unittest.mock import MagicMock, patch

import pytest

from api.routers.analysis import _local_analysis_runs, revoke_analysis_run


def _make_run(user_id="user-1", status="running"):
    """建立一個測試用的 analysis run dict 並註冊到 _local_analysis_runs。"""
    run_id = "test-run-123"
    run = {
        "run_id": run_id,
        "session_id": "sess-1",
        "user_id": user_id,
        "status": status,
        "content": "",
        "error": None,
        "started_at": 1000.0,
        "finished_at": None,
        "next_event_id": 1,
        "events": [],
        "_last_sync": 0.0,
        "_subscribers": set(),
        "revoked": False,
        "_invoke_task": None,
    }
    _local_analysis_runs[run_id] = run
    return run


@pytest.fixture(autouse=True)
def _clear_runs(monkeypatch):
    # 同一 IP 的撤銷端點限流 10/分鐘；這檔的案例數超過就會被 429 擋掉
    from api.routers.analysis import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    _local_analysis_runs.clear()
    yield
    _local_analysis_runs.clear()


def _mock_request():
    """使用真 Starlette Request（slowapi @limiter.limit 要求）。"""
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/analyze/test-run-123/revoke",
        "headers": [(b"user-agent", b"test-client")],
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return StarletteRequest(scope)


def _mock_user(user_id="user-1"):
    return {"user_id": user_id, "username": "tester"}


class TestRevokeAnalysisRun:
    """revoke_analysis_run 的信任層撤銷路徑。"""

    @pytest.mark.asyncio
    async def test_successful_revoke_cancels_task(self):
        """撤銷成功：標記 revoked=True + cancel invoke_task。"""
        run = _make_run()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        run["_invoke_task"] = mock_task

        with patch("core.audit.AuditLogger.log") as mock_log:
            result = await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        assert result["success"] is True
        assert result["revoked"] is True
        assert result["cancelled"] is True
        assert run["revoked"] is True
        mock_task.cancel.assert_called_once()
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["action"] == "agent_revoked"

    @pytest.mark.asyncio
    async def test_revoke_other_users_run_returns_404(self):
        """撤銷別人的 run → 404（不洩漏 run_id 存在性）。"""
        _make_run(user_id="user-owner")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user(user_id="user-attacker")
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_run_returns_404(self):
        """撤銷不存在的 run → 404。"""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await revoke_analysis_run(
                "nonexistent", _mock_request(), _mock_user()
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", ["completed", "error", "timeout", "cancelled", "waiting"]
    )
    async def test_revoke_settled_run_returns_409(self, status):
        """撤銷已結束的 run → 409 衝突。

        成功狀態是 completed（舊碼寫成從沒被指派過的 "finished"，
        於是已完成的 run 還能被「撤銷」、寫一筆假的 agent_revoked 稽核）。
        """
        _make_run(status=status)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_409(self):
        """重複撤銷已撤銷的 run → 409。"""
        _make_run(status="revoked")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_task_already_done(self):
        """invoke_task 已完成時撤銷 → cancelled=False 但仍標記 revoked。"""
        run = _make_run()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        run["_invoke_task"] = mock_task

        with patch("core.audit.AuditLogger.log"):
            result = await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        assert result["cancelled"] is False
        assert run["revoked"] is True
        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_detached_run_still_allowed(self):
        """斷線交給背景跑的 run（detached）仍在執行，要能撤銷。"""
        run = _make_run(status="detached")
        mock_task = MagicMock()
        mock_task.done.return_value = False
        run["_invoke_task"] = mock_task

        with patch("core.audit.AuditLogger.log"):
            result = await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        assert result["cancelled"] is True
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_mode_revoke_publishes_control(self):
        """worker 模式本機沒有 invoke_task → 要走 control channel 通知 worker。

        舊碼只 cancel 本機 task：worker 模式下 _invoke_task 永遠是 None，
        撤銷回 success 但 worker 照跑、照燒 token。
        """
        run = _make_run()
        assert run["_invoke_task"] is None

        with (
            patch("core.audit.AuditLogger.log"),
            patch(
                "core.analysis_queue.publish_control", return_value=True
            ) as mock_publish,
        ):
            result = await revoke_analysis_run(
                "test-run-123", _mock_request(), _mock_user()
            )

        mock_publish.assert_called_once_with("test-run-123", "revoke")
        assert result["cancelled"] is True
        assert run["revoked"] is True

    @pytest.mark.asyncio
    async def test_in_process_done_task_does_not_publish_control(self):
        """in-process 且 task 已結束：不該再發 control 訊號。"""
        run = _make_run()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        run["_invoke_task"] = mock_task

        with (
            patch("core.audit.AuditLogger.log"),
            patch("core.analysis_queue.publish_control") as mock_publish,
        ):
            await revoke_analysis_run("test-run-123", _mock_request(), _mock_user())

        mock_publish.assert_not_called()
