"""/api/chat/scam-confirm endpoint 測試。

驗證 Trustworthy AI HITL 場景 B 的確認 API：
- DB 反查驗證（不信任 client 傳的 verdict）
- audit log 寫入（action=scam_verdict_confirmed）
- 競改偵測（client verdict 與 server 不符時用 server 值）
- 404（該 session 無 scam_evidence）

對齊 test_chat_session_create.py 的 patch run_sync 風格。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

USER_ID = "test-user-001"


@pytest.mark.integration
class TestScamConfirmEndpoint:
    @pytest.mark.asyncio
    async def test_confirms_and_writes_audit_when_verdict_matches(self, client, auth_headers):
        """verdict 與 DB 反查相符 → 寫 audit log，回 success。"""
        fake_history = [
            {
                "role": "assistant",
                "content": "這是高風險代幣",
                "metadata": {
                    "scam_evidence": {
                        "verdict": "high_risk",
                        "confidence": 75,
                        "requires_ack": True,
                    }
                },
            }
        ]

        # 直接 patch 底層 DB 函式（check_session_ownership / get_chat_history），
        # run_sync 走真實執行——endpoint 現在有 3 個 run_sync 呼叫
        # （ownership 檢查 → 歷史反查 → audit），call-count 假 run_sync
        # 會把 ownership 那次誤回成 fake_history 而漏 mock 歷史反查（打到真 DB）。
        with (
            patch("api.routers.analysis.check_session_ownership", return_value=True),
            patch("api.routers.analysis.get_chat_history", return_value=fake_history),
            patch("core.audit.AuditLogger.log") as mock_log,
        ):
            response = await client.post(
                "/api/chat/scam-confirm",
                json={"session_id": "sess-1", "verdict": "high_risk"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_log.assert_called_once()
        # 確認 audit 用 server verdict（不是 client）
        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["action"] == "scam_verdict_confirmed"
        assert call_kwargs["metadata"]["verdict"] == "high_risk"

    @pytest.mark.asyncio
    async def test_404_when_no_scam_evidence_in_session(self, client, auth_headers):
        """該 session 無 scam_evidence → 404。"""
        fake_history = [
            {"role": "assistant", "content": "一般回應", "metadata": None},
        ]

        with (
            patch("api.routers.analysis.check_session_ownership", return_value=True),
            patch("api.routers.analysis.get_chat_history", return_value=fake_history),
        ):
            response = await client.post(
                "/api/chat/scam-confirm",
                json={"session_id": "sess-1", "verdict": "high_risk"},
                headers=auth_headers,
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_uses_server_verdict_when_client_tampered(self, client, auth_headers):
        """client 傳的 verdict 與 server 不符（竄改）→ 仍用 server 值寫 audit。"""
        fake_history = [
            {
                "role": "assistant",
                "content": "高風險",
                "metadata": {"scam_evidence": {"verdict": "high_risk", "confidence": 80}},
            }
        ]

        with (
            patch("api.routers.analysis.check_session_ownership", return_value=True),
            patch("api.routers.analysis.get_chat_history", return_value=fake_history),
            patch("core.audit.AuditLogger.log") as mock_log,
        ):
            # client 故意傳 warning（試圖降級）
            response = await client.post(
                "/api/chat/scam-confirm",
                json={"session_id": "sess-1", "verdict": "warning"},
                headers=auth_headers,
            )

        assert response.status_code == 200  # 仍成功，但用 server 值
        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["metadata"]["verdict"] == "high_risk"  # server 值
        assert call_kwargs["metadata"]["client_verdict"] == "warning"  # 記錄竄改企圖

    @pytest.mark.asyncio
    async def test_metadata_string_json_handled(self, client, auth_headers):
        """metadata 是 JSON 字串（部分 DB 序列化）也能解析。"""
        import json

        fake_history = [
            {
                "role": "assistant",
                "content": "高風險",
                "metadata": json.dumps(
                    {"scam_evidence": {"verdict": "warning", "confidence": 30}}
                ),
            }
        ]

        with (
            patch("api.routers.analysis.check_session_ownership", return_value=True),
            patch("api.routers.analysis.get_chat_history", return_value=fake_history),
            patch("core.audit.AuditLogger.log") as mock_log,
        ):
            response = await client.post(
                "/api/chat/scam-confirm",
                json={"session_id": "sess-1", "verdict": "warning"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert mock_log.call_args.kwargs["metadata"]["verdict"] == "warning"
