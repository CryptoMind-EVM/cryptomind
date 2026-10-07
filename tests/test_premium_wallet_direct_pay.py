"""Premium 錢包直付接線測試（docs/plans/premium-wallet-direct-pay.md）。

DANNY 2026-09-11 拍板「錢包直付為主＋手動爲輔」：有注入錢包時面板主按鈕
以 eth_sendTransaction 送出預填 USDC transfer（chainId 先檢查/切鏈，
calldata 帶訂單精確 micro），取得 tx hash 後以 receipt 驗證；手動轉帳
（交易所提幣）收進 <details> 次要區塊，行為不變。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
PREMIUM_JS = (REPO / "web/js/premium.js").read_text(encoding="utf-8")
# 送款本體（calldata、切鏈）2026-09-25 抽到 usdc-pay.js，premium 與論壇共用
USDC_PAY_JS = (REPO / "web/js/usdc-pay.js").read_text(encoding="utf-8")
PAYMENT_RAILS = (REPO / "api/payment_rails.py").read_text(encoding="utf-8")
PREMIUM_ROUTER = (REPO / "api/routers/premium.py").read_text(encoding="utf-8")


class TestBackendReceiptVerification:
    def test_verify_evm_usdc_tx_exists(self):
        assert "async def verify_evm_usdc_tx" in PAYMENT_RAILS

    def test_receipt_path_same_attribution_rules(self):
        """歸因 fail-closed 不變：payer 綁定、精確 micro、iat 時間窗、確認數。"""
        for fragment in (
            "payer binding",
            "value_micro != expected_micro",
            "EVM_CONFIRMATIONS",
            "min_ts",
        ):
            assert fragment in PAYMENT_RAILS

    def test_upgrade_dispatches_on_tx_hash(self):
        """body.tx_hash（0x＋66）→ receipt 驗證；否則維持 getLogs 掃描。"""
        assert "verify_evm_usdc_tx(" in PREMIUM_ROUTER
        assert 'body.tx_hash.startswith("0x")' in PREMIUM_ROUTER
        assert "len(body.tx_hash) == 66" in PREMIUM_ROUTER
        # 手動路徑保留
        assert "verify_evm_usdc_payment(" in PREMIUM_ROUTER

    def test_order_response_carries_micro_for_calldata(self):
        assert '"micro": quote["micro"]' in PREMIUM_ROUTER


class TestFrontendWalletPay:
    def test_dual_mode_panel_wallet_primary(self):
        assert "wallet-pay-btn" in PREMIUM_JS
        assert "manualTabTitle" in PREMIUM_JS
        assert "<details" in PREMIUM_JS  # 手動轉帳收進次要區塊

    def test_wallet_send_uses_erc20_transfer_calldata(self):
        """USDC transfer(address,uint256)：selector a9059cbb＋pad 地址＋pad 金額。"""
        assert "return walletSendUsdc(order, bound);" in PREMIUM_JS
        assert "0xa9059cbb" in USDC_PAY_JS
        assert "padStart(64, '0')" in USDC_PAY_JS
        assert "BigInt(m).toString(16)" in USDC_PAY_JS

    def test_chain_check_before_send(self):
        """錯鏈直付＝資產損失：eth_chainId 檢查＋wallet_switchEthereumChain。"""
        assert "eth_chainId" in USDC_PAY_JS
        assert "wallet_switchEthereumChain" in USDC_PAY_JS
        assert "wallet_addEthereumChain" in USDC_PAY_JS
        assert "'0x2105'" in USDC_PAY_JS  # Base mainnet
        assert "'0x14a34'" in USDC_PAY_JS  # Base Sepolia

    def test_claim_submits_tx_hash(self):
        assert "tx_hash: txHash || undefined" in PREMIUM_JS
        assert "eth_sendTransaction" in USDC_PAY_JS

    def test_wallet_rejection_surfaced_kindly(self):
        assert "walletRejected" in PREMIUM_JS


class TestI18nWalletPay:
    @pytest.mark.parametrize(
        "lang", ["en", "zh-TW", "zh-CN", "ru"], ids=["en", "zhTW", "zhCN", "ru"]
    )
    def test_wallet_pay_keys_exist(self, lang: str):
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        prem = data["premium"]
        for key in (
            "walletPayCta",
            "walletPayHint",
            "walletPaySending",
            "walletRejected",
            "walletNoAccount",
            "chainSwitchNeeded",
            "manualTabTitle",
        ):
            assert prem.get(key), f"{lang} 缺 premium.{key}"
