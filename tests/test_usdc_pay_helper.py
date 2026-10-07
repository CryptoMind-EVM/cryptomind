"""USDC on Base 錢包直付共用模組（web/js/usdc-pay.js）。

2026-09-25 論壇付款從 TON 改 USDC on Base：跟 premium 走同一條錢包直付，所以把
premium.js 的送款（calldata、切鏈、付款帳號必須是綁定錢包）抽成共用模組，premium
與論壇都 import 它，不再各寫一份。行為在 tests/js/usdc_pay.mjs 用假錢包實跑。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USDC_PAY = REPO / "web/js/usdc-pay.js"
PREMIUM = REPO / "web/js/premium.js"
FORUM_APP = REPO / "web/js/forum-app.js"


def test_node_gate_passes():
    try:
        result = subprocess.run(
            ["node", "tests/js/usdc_pay.mjs"],
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


class TestSharedNotCopied:
    def test_premium_and_forum_import_the_shared_sender(self):
        premium = PREMIUM.read_text(encoding="utf-8")
        forum = FORUM_APP.read_text(encoding="utf-8")
        assert "from './usdc-pay.js'" in premium
        assert "from './usdc-pay.js'" in forum
        # 送款本體只有一份：calldata selector 只出現在共用模組
        assert "0xa9059cbb" in USDC_PAY.read_text(encoding="utf-8")
        assert "0xa9059cbb" not in premium
        assert "0xa9059cbb" not in forum
        assert "walletSendUsdc(" in forum

    def test_premium_delegates_wallet_send(self):
        premium = PREMIUM.read_text(encoding="utf-8")
        body = premium[premium.index("async _walletSendUsdc(order, bound)") :]
        body = body[: body.index("\n    }\n")]
        assert "return walletSendUsdc(order, bound);" in body

    def test_payer_check_before_send_in_shared_module(self):
        src = USDC_PAY.read_text(encoding="utf-8")
        start = src.index("async function walletSendUsdc(order, bound)")
        assert src.index("resolvePaymentPayer(bound, from)", start) < src.index(
            "eth_sendTransaction", start
        )
        assert src.index("eth_chainId", start) < src.index("eth_sendTransaction", start)

    def test_calldata_goes_through_builder_code(self):
        src = USDC_PAY.read_text(encoding="utf-8")
        assert "import { withBuilderCode } from './builder-code.js';" in src
        assert re.search(
            r"const data = withBuilderCode\('0xa9059cbb' \+ padAddr\(order\.receiving_address\) \+ padAmount\(order\.micro\)\);",
            src,
        )


def test_usdc_pay_gets_its_own_chunk_above_forum_app_core():
    """premium（shared-core）import usdc-pay。沒有自己的高優先權 chunk 的話，
    forum-app-core 會連依賴把它吃掉，shared-core 反過來 import forum-app-core——
    SPA 開 Settings 就把整個論壇模組載進來（實測 vite build 的 chunk 圖）。"""
    cfg = (REPO / "vite.config.js").read_text(encoding="utf-8")
    group = re.search(r"\{ name: 'usdc-pay', priority: (\d+),[^}]*\}", cfg)
    forum = re.search(r"\{ name: 'forum-app-core', priority: (\d+),", cfg)
    assert group and forum
    assert int(group.group(1)) > int(forum.group(1))
    assert "usdc-pay|builder-code" in group.group(0)


class TestForumTonGone:
    def test_ton_pay_module_removed(self):
        assert not (REPO / "web/js/forum/ton-pay.js").exists()

    @pytest.mark.parametrize("page", ["post.html", "create.html"])
    def test_forum_pages_do_not_load_tonconnect_or_tonweb(self, page):
        html = (REPO / "web/forum" / page).read_text(encoding="utf-8")
        assert "tonconnect-ui" not in html
        assert "tonweb" not in html.lower()

    def test_forum_app_has_no_ton_payment(self):
        """TON 付款流程與寫死的 TON 金額標籤都拿掉（舊紀錄照 tx hash 形狀標 TON）。"""
        src = FORUM_APP.read_text(encoding="utf-8")
        for needle in (
            "ton-order",
            "executeForumTonPayment",
            "GRAM",
            "tips_total} TON",
            "toFixed(1)} TON",
            '<span class="text-sm">TON</span>',
            "author_ton_tippable",
        ):
            assert needle not in src, needle

    def test_paid_but_post_failed_modal_survives_missing_security_utils(self):
        """發文頁沒載 security-utils.js：「已付款但發文失敗」視窗不能直接呼叫
        SecurityUtils（ReferenceError＝使用者看不到 tx hash、按鈕卡死），而且
        txHash 要是真的錢包 tx hash（以前是未宣告變數）。"""
        src = FORUM_APP.read_text(encoding="utf-8")
        assert "SecurityUtils.escapeHTML(txHash" not in src
        assert "${_esc(txHash || '')}" in src
        assert "let txHash = null; // 錢包送出的 tx hash" in src

    def test_forum_payments_blocked_inside_telegram(self):
        """Telegram Mini App 內不給付款介面，只給「用瀏覽器開啟」。"""
        src = FORUM_APP.read_text(encoding="utf-8")
        tip = src[src.index("async handleTip(postId)") :]
        tip = tip[: tip.index("// Create Post Logic")]
        assert tip.index("isTelegramMiniApp()") < tip.index("/tip/payment-order")
        create = src[src.index("'/api/forum/posts/payment-order'") - 3000 :]
        assert "isTelegramMiniApp()" in create[:3000]
        assert "showForumTmaPayNotice" in src
