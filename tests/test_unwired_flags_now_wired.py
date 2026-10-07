"""兩個幽靈旗標接線後的行為測試（2026-09-04）。

TRUST_EVENT_RECOMPUTE_ENABLED 與 WALLET_MONITOR_EVM_ENABLED 自建立起就沒有
任何模組讀它們——開關看起來在，實際完全沒作用。這支測它們現在真的管事，
而且管的是對的東西。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestTrustEventRecompute:
    """檢舉錢包 → 立刻重算該錢包主人的信任分數（原本只有每天 00:30 的 cron）。"""

    async def test_flag_off_does_nothing(self):
        """斷言「沒被呼叫」，不能靠例外傳播——helper 的 except Exception 會把
        AssertionError 一起吞掉，那樣旗標判斷整個拔掉測試也照樣綠。"""
        from api.routers.scam_tracker import reports

        lookup = AsyncMock()
        with patch.object(reports, "TRUST_EVENT_RECOMPUTE_ENABLED", False), \
                patch.object(reports.user_repo, "get_by_id", new=lookup):
            await reports._recompute_trust_on_scam_hit("EQabc")
        lookup.assert_not_awaited()

    async def test_non_user_wallet_is_not_recomputed(self):
        """被檢舉的地址多半不是平台使用者——不可替非使用者建 trust 列。"""
        from api.routers.scam_tracker import reports

        recompute = AsyncMock()
        with patch.object(reports, "TRUST_EVENT_RECOMPUTE_ENABLED", True), \
                patch.object(reports.user_repo, "get_by_id", new=AsyncMock(return_value=None)), \
                patch("core.identity.scoring.recompute_user_trust", new=recompute):
            await reports._recompute_trust_on_scam_hit("EQstranger")
        recompute.assert_not_awaited()

    async def test_platform_user_is_recomputed_with_original_case(self):
        """**不可傳 upper 過的地址**：scam_reports 存 .upper()，但 user_id
        （TON 用戶即錢包地址）是原始大小寫，upper 過去永遠對不到人。"""
        from api.routers.scam_tracker import reports

        recompute = AsyncMock(return_value={"score": 10})
        addr = "EQmIxEdCaSe123"
        with patch.object(reports, "TRUST_EVENT_RECOMPUTE_ENABLED", True), \
                patch.object(reports.user_repo, "get_by_id", new=AsyncMock(return_value={"user_id": addr})), \
                patch("core.identity.scoring.recompute_user_trust", new=recompute):
            await reports._recompute_trust_on_scam_hit(addr)
        recompute.assert_awaited_once_with(addr, wallet_address=addr, reason="event_scam_hit")

    async def test_failure_never_breaks_the_report(self):
        """報告已經寫進 DB 了——重算失敗不得讓 endpoint 噴錯。"""
        from api.routers.scam_tracker import reports

        with patch.object(reports, "TRUST_EVENT_RECOMPUTE_ENABLED", True), \
                patch.object(reports.user_repo, "get_by_id",
                             new=AsyncMock(side_effect=RuntimeError("db down"))):
            await reports._recompute_trust_on_scam_hit("EQabc")  # 不得拋出


class TestWalletMonitorEvmFlag:
    def test_evm_branch_is_gated(self):
        """旗標關掉時 EVM 路徑不得執行（原本是無條件跑）。"""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1] / "scripts/cron_wallet_monitor.py"
        ).read_text(encoding="utf-8")
        assert "elif not WALLET_MONITOR_EVM_ENABLED:" in src
        # 閘門必須排在 adapter 之前，否則就只是裝飾
        assert src.index("elif not WALLET_MONITOR_EVM_ENABLED:") < src.index("get_adapter")

    def test_flag_still_requires_the_service_key(self):
        """沒有 ETHERSCAN_SERVICE_API_KEY 時一律 False——那個前提要保住。"""
        from pathlib import Path

        cfg = (Path(__file__).resolve().parents[1] / "core/config.py").read_text(encoding="utf-8")
        block = cfg[cfg.index("WALLET_MONITOR_EVM_ENABLED = ("):]
        assert "and bool(ETHERSCAN_SERVICE_API_KEY)" in block[:300]


class TestDebtListIsEmpty:
    def test_no_flag_remains_unwired(self):
        import core.feature_flags as ff

        assert ff.UNWIRED_FLAGS == set(), (
            f"還有未接線的旗標：{sorted(ff.UNWIRED_FLAGS)}"
        )
