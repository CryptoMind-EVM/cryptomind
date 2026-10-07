"""付款前綁定檢查（2026-09-11 盤查 🔴 項）。

後端 /api/premium/upgrade 的付款人綁定是 fail-closed：USDC 不是從已綁定地址
送出就永遠驗不過。以前前端在建單前不查綁定、錢包直付也不比對付款帳號——
Telegram 登入者（常常沒綁定）錢先轉出去，claim 才被擋，錢卡住要人工處理。

守三件事：
1. 判定純函式（resolvePaymentPayer）——node 斷言看守，這裡做 pytest 包裝。
2. 接線：handleUpgradeClick 在建單前先 _ensurePayerBound；_walletSendUsdc 在
   eth_sendTransaction 前先 resolvePaymentPayer。
3. 綁定入口真的存在：後端有 GET /api/user/wallets、evm-auth 有 safeEvmBind、
   delegator 白名單認得它、Settings 未綁定卡片掛了這顆按鈕。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
PREMIUM = REPO / "web" / "js" / "premium.js"
USDC_PAY = REPO / "web" / "js" / "usdc-pay.js"
EVM_AUTH = REPO / "web" / "js" / "evm-auth.js"
DELEGATOR = REPO / "web" / "js" / "click-delegator.js"
SETTINGS = REPO / "web" / "js" / "components" / "tab-settings.js"
USER_ROUTER = REPO / "api" / "routers" / "user.py"


def test_node_gate_passes():
    try:
        result = subprocess.run(
            ["node", "tests/js/premium_payer_binding.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("node 不可用")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout


class TestWiring:
    def test_order_creation_waits_for_binding(self):
        js = PREMIUM.read_text(encoding="utf-8")
        start = js.index("async handleUpgradeClick()")
        end = js.index("async chooseRail()", start)
        body = js[start:end]
        bound_at = body.index("_ensurePayerBound()")
        order_at = body.index("startStableRailUpgrade(")
        assert bound_at < order_at, "要先確保綁定再建單"
        assert re.search(
            r"if \(Array\.isArray\(bound\) && !bound\.length\) return;", body
        ), "沒綁定也沒完成綁定時不能建單"

    def test_direct_pay_checks_payer_before_sending(self):
        # 送款本體 2026-09-25 抽到 usdc-pay.js（premium 與論壇共用）
        assert "return walletSendUsdc(order, bound);" in PREMIUM.read_text(
            encoding="utf-8"
        )
        js = USDC_PAY.read_text(encoding="utf-8")
        start = js.index("async function walletSendUsdc(order, bound)")
        check_at = js.index("resolvePaymentPayer(bound, from)", start)
        send_at = js.index("eth_sendTransaction", start)
        assert check_at < send_at, "付款帳號比對必須在送交易之前"

    def test_bind_entry_points_exist(self):
        assert '@router.get("/api/user/wallets")' in USER_ROUTER.read_text(
            encoding="utf-8"
        )
        assert "window.safeEvmBind = async function" in EVM_AUTH.read_text(
            encoding="utf-8"
        )
        assert "'/api/user/wallets/bind'" in EVM_AUTH.read_text(encoding="utf-8")
        assert "'safeEvmBind'" in DELEGATOR.read_text(encoding="utf-8"), (
            "delegator 白名單漏了就是死鍵"
        )
        assert 'data-click="safeEvmBind"' in SETTINGS.read_text(encoding="utf-8")

    def test_manual_copy_no_longer_promises_exchange_withdrawal(self):
        js = PREMIUM.read_text(encoding="utf-8")
        assert "exchange withdrawal')" not in js, (
            "交易所提幣是從交易所地址送出，payer binding 永遠對不上——文案不能再承諾"
        )
