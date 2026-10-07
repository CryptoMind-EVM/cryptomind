"""付款紀錄多資產顯示（2026-09-12 DANNY 截圖）。

問題：``membership_payments.amount`` 不管走哪條軌都是「TON 定價 × 月數」，
前端又一律印 TON，於是 EVM 使用者的 USDC 訂單與 admin 授予都顯示成「-1 TON」；
點明細跳的是瀏覽器 alert，連換行符號都原樣印出來。

守三件事：
1. 後端 ``annotate_payment_row`` 依 tx_hash 形狀補 asset／display_amount／
   is_admin_grant（admin_grant_ 前綴＝授予無金額；0x+64 hex＝USDC on Base，
   金額＝USD 錨定 × 月數；其餘＝TON）。
2. 前端純函式（formatTxAmount／summarizeTotals）與 showDetail 走 ContentModal
   ——node 斷言看守，這裡做 pytest 包裝。
3. 舊的 ``wallet.transactionDetails``（帶字面 \\n 的 alert 文案）從 4 個語系
   與 wallet.js 都退場。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
WALLET_JS = REPO / "web" / "js" / "wallet.js"


class TestAnnotatePaymentRow:
    def _row(self, **kw):
        base = {
            "type": "membership",
            "amount": 1.0,
            "months": 1,
            "tx_hash": "",
            "title": "Premium Membership (1 Month)",
        }
        base.update(kw)
        return base

    def test_admin_grant_has_no_amount(self):
        from core.database.forum import annotate_payment_row

        row = annotate_payment_row(
            self._row(tx_hash="admin_grant_evm_0xabc_evm_0xdef_1789027826")
        )
        assert row["is_admin_grant"] is True
        assert row["kind"] == "admin_grant"
        assert row["asset"] is None and row["display_amount"] is None

    def test_evm_hash_means_usdc_at_usd_anchor(self, monkeypatch):
        from core.database import forum

        monkeypatch.setattr(
            "core.config.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        )
        row = forum.annotate_payment_row(self._row(tx_hash="0x" + "a" * 64, months=1))
        assert (
            row["asset"] == "USDC"
            and row["chain"] == "base"
            and row["rail"] == "evm_usdc"
        )
        assert row["display_amount"] == 12.0
        assert row["is_admin_grant"] is False
        # 年繳走年價，不是 12 × 月價
        assert (
            forum.annotate_payment_row(self._row(tx_hash="0x" + "b" * 64, months=12))[
                "display_amount"
            ]
            == 108.0
        )
        assert (
            forum.annotate_payment_row(self._row(tx_hash="0x" + "c" * 64, months=13))[
                "display_amount"
            ]
            == 120.0
        )

    def test_other_hash_stays_ton_with_stored_amount(self):
        from core.database.forum import annotate_payment_row

        row = annotate_payment_row(
            self._row(tx_hash="te6cckEBAQEA", amount=6.0, months=1)
        )
        assert row["asset"] == "TON" and row["display_amount"] == 6.0
        assert row["is_admin_grant"] is False

    def test_legacy_post_rows_are_ton(self):
        from core.database.forum import annotate_payment_row

        row = annotate_payment_row(
            {"type": "post", "amount": 0.1, "months": None, "tx_hash": "abc"}
        )
        assert (
            row["kind"] == "post"
            and row["asset"] == "TON"
            and row["display_amount"] == 0.1
        )

    def test_post_rows_with_evm_hash_are_usdc_at_forum_fee(self, monkeypatch):
        """2026-09-25 起發文費是 USDC on Base：0x hash 的發文列顯示 USD 發文費，
        不是 SQL 帶進來的 TON 價。"""
        from core.database.forum import annotate_payment_row

        monkeypatch.setattr("core.config.FORUM_POST_FEE_USD", 0.5)
        row = annotate_payment_row(
            {"type": "post", "amount": 0.1, "months": None, "tx_hash": "0x" + "d" * 64}
        )
        assert row["kind"] == "post"
        assert row["asset"] == "USDC" and row["chain"] == "base"
        assert row["rail"] == "evm_usdc"
        assert row["display_amount"] == 0.5


class TestTipRowAssets:
    def test_tip_rows_labelled_by_hash_shape(self):
        from core.database.forum import annotate_tip_row

        usdc = annotate_tip_row({"amount": 1.0037, "tx_hash": "0x" + "e" * 64})
        assert usdc["asset"] == "USDC" and usdc["chain"] == "base"
        ton = annotate_tip_row({"amount": 0.1, "tx_hash": "te6cckEBAQEA"})
        assert ton["asset"] == "TON" and ton["chain"] == "ton"

    def test_tip_endpoints_annotate_rows(self):
        src = (REPO / "core" / "database" / "forum.py").read_text(encoding="utf-8")
        for fn in ("def get_tips_sent", "def get_tips_received"):
            body = src[src.index(fn) :]
            body = body[: body.index("\ndef ", 10)]
            assert "annotate_tip_row(" in body, fn

    def test_received_total_counts_usdc_only(self):
        """總額標 USDC：舊的 TON 打賞不能混進同一個數字（列表照樣顯示、標 TON）。"""
        src = (REPO / "core" / "database" / "forum.py").read_text(encoding="utf-8")
        body = src[src.index("def get_tips_total_received") :]
        body = body[: body.index("\ndef ", 10)]
        assert "tx_hash LIKE '0x%%'" in body
        orm = (REPO / "core" / "orm" / "forum_repo.py").read_text(encoding="utf-8")
        body = orm[orm.index("async def get_tips_total_received") :]
        body = body[: body.index("async def ", 10)]
        assert 'Tip.tx_hash.like("0x%")' in body

    def test_history_sql_carries_months_for_both_branches(self):
        src = (REPO / "core" / "database" / "forum.py").read_text(encoding="utf-8")
        assert "NULL::integer as months" in src, (
            "posts 分支要補 months 欄位，UNION 才對得齊"
        )
        assert (
            "SELECT type, id, title, amount, months, tx_hash, created_at FROM (" in src
        )
        assert "annotate_payment_row(r)" in src


def test_node_gate_passes():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "wallet_history_assets.mjs")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "wallet_history_assets: ok" in proc.stderr


def test_wallet_js_uses_platform_modal_not_bare_alert():
    src = WALLET_JS.read_text(encoding="utf-8")
    assert "window.ContentModal.open(" in src
    assert "wallet.transactionDetails" not in src, "舊的 alert 文案 key 要退場"
    # 只允許最後一道 fallback 的 window.alert；不准再有裸 alert(...) 當主路徑
    assert src.count("alert(") == 1 and "window.alert(body)" in src


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales_have_detail_keys_and_drop_old_alert_key(lang):
    data = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    wallet = data["wallet"]
    for key in (
        "txTypeAdminGrant",
        "detailTitle",
        "detailType",
        "detailAmount",
        "detailChain",
        "detailDate",
        "detailHash",
        "grantNoAmount",
    ):
        assert key in wallet, f"{lang} 缺 wallet.{key}"
    assert "transactionDetails" not in wallet


def test_totals_markup_no_longer_hardcodes_ton():
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    assert '<span id="wallet-total-out">0</span>' in html
    assert '<span id="wallet-total-out">0.0</span> TON' not in html
