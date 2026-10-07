"""後台「核准檢舉（隱藏內容）」回歸測試。

GovernanceRepository.finalize_report 呼叫 _add_violation_points_tx 時誤傳
author_id=（該函式為 keyword-only 的 user_id=），後台按「Approve」必定
TypeError → 500，檢舉永遠無法結案。這裡不 mock _add_violation_points_tx，
讓真實函式簽章被執行到。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.orm.governance_repo import GovernanceRepository


def _fake_session():
    s = MagicMock()
    report_row = MagicMock()
    report_row.fetchone.return_value = ("reporter-1", "post", 5)
    s.execute = AsyncMock(side_effect=[report_row] + [MagicMock()] * 10)
    s.flush = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_finalize_report_approved_adds_violation_points_to_author():
    repo = GovernanceRepository()
    s = _fake_session()
    with (
        patch.object(
            GovernanceRepository,
            "_get_content_author_tx",
            AsyncMock(return_value="author-1"),
        ),
        patch.object(
            GovernanceRepository,
            "_get_user_violation_points_tx",
            AsyncMock(return_value=0),
        ),
        patch.object(
            GovernanceRepository, "_get_report_votes_tx", AsyncMock(return_value=[])
        ),
        patch.object(GovernanceRepository, "_apply_suspension_tx", AsyncMock()),
    ):
        result = await repo.finalize_report(1, "approved", "mild", "admin-1", session=s)

    assert result["success"] is True
    assert result["points_assigned"] > 0
    violation = s.add.call_args.args[0]
    assert violation.user_id == "author-1"


@pytest.mark.asyncio
async def test_finalize_report_approved_without_author_just_closes():
    """內容已刪（查不到作者）→ 仍結案，但不記點（避免寫入 user_id=None）"""
    repo = GovernanceRepository()
    s = _fake_session()
    with (
        patch.object(
            GovernanceRepository, "_get_content_author_tx", AsyncMock(return_value=None)
        ),
        patch.object(
            GovernanceRepository, "_get_report_votes_tx", AsyncMock(return_value=[])
        ),
    ):
        result = await repo.finalize_report(1, "approved", "mild", "admin-1", session=s)

    assert result["success"] is True
    assert result["points_assigned"] == 0
    s.add.assert_not_called()
