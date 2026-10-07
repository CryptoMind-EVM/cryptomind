"""前端功能盤查（2026-09-25）修正的守衛。

行為測試在 tests/js/*.mjs（node 實跑：市場頁深度分析快取、價格警報清單、
私訊舊訊息順序、帳本競態／重複送出／類別合併／-0、金鑰移除確認）；
這裡包成 pytest 閘門，加上後端交易雜湊驗證與幾個跑不起來的頁面的靜態檢查。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _require_node() -> None:
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")


def _node(script: str, marker: str) -> None:
    _require_node()
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert marker in proc.stderr


# ── 行為測試（node）────────────────────────────────────────────


def test_market_deep_analysis_node_gate():
    """六個市場頁：快取存在送出時的代號底下、錯誤回應不進快取（深度與一般分析）。"""
    _node("market_deep_analysis.mjs", "market_deep_analysis: ok")


def test_market_ws_ticker_node_gate():
    """Ticker WS：0% 漲跌中性色（字串／-0）、tick 不蓋掉 RSI 清單的 RSI。"""
    _node("market_ws_ticker.mjs", "market_ws_ticker: ok")


def test_frontend_feature_bugs_node_gate():
    """警報清單／私訊順序／帳本競態與重複送出／類別合併／-0／刪除確認。"""
    _node("frontend_feature_bugs.mjs", "frontend_feature_bugs: ok")


# ── 1) 詐騙追蹤：評論的交易雜湊 ────────────────────────────────


class TestCommentTxHash:
    def _model(self):
        from api.routers.scam_tracker.models import CommentCreate

        return CommentCreate

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("a" * 64, "a" * 64),  # TON
            ("0x" + "b" * 64, "0x" + "b" * 64),  # EVM（以前 max_length=64 會擋掉）
            ("0X" + "AB" * 32, "0x" + "ab" * 32),  # 大寫統一轉小寫
            (None, None),
        ],
    )
    def test_accepts_ton_and_evm(self, raw, expected):
        m = self._model()(content="x" * 10, transaction_hash=raw)
        assert m.transaction_hash == expected

    @pytest.mark.parametrize(
        "raw",
        [
            # 64 字元但不是 hex（以前只驗長度，會通過）
            "<img src=x onerror=alert(1)>" + "a" * 36,
            "g" * 64,
            "a" * 63,
            "0x" + "a" * 63,
            "a" * 65,
        ],
    )
    def test_rejects_non_hex(self, raw):
        with pytest.raises(ValidationError):
            self._model()(content="x" * 10, transaction_hash=raw)

    def test_render_escapes_comment_tx_hash(self):
        # 兩份 renderComments（原版與 i18n 補丁）都要 escape
        js = _read("web/scam-tracker/js/scam-tracker.js")
        assert "TX: ${comment.transaction_hash}" not in js
        assert "TX: ${this.escapeHTML(comment.transaction_hash)}" in js
        patch = _read("web/scam-tracker/js/scam-tracker-i18n.js")
        assert "TX: ${escapeHtml(comment.transaction_hash)}" in patch
        # 送出前先擋格式錯誤（否則 422 的 detail 陣列會變成 [object Object]）
        assert js.count("if (txHash && !isValidTxHash(txHash))") == 2  # 舉報＋評論
        assert "!isValidTxHash(txHash)" in patch


# ── 3) 價格警報清單 ─────────────────────────────────────────────


def test_alert_list_containers_exist_in_stock_tabs():
    for tab in ("twstock", "usstock"):
        tpl = _read(f"web/js/components/tab-{tab}.js")
        assert f'id="alert-list-section-{tab}" class="hidden' in tpl, tab
        assert f'id="alert-list-{tab}"' in tpl, tab
        assert 'data-i18n="modals.priceAlert.myAlerts"' in tpl, tab
    alerts = _read("web/js/alerts.js")
    assert "'alert-list-twstock': 'tw_stock'" in alerts
    assert "'alert-list-usstock': 'us_stock'" in alerts
    # 市場值要跟鈴鐺送出的一致，不然清單永遠空
    assert "JSON.stringify([sym, 'tw_stock'])" in _read("web/js/twstock.js")
    assert "JSON.stringify([sym, 'us_stock'])" in _read("web/js/usstock.js")


def test_dead_alert_duplicate_removed():
    assert not (WEB / "js" / "modals.js").exists()
    assert "import './modals.js'" not in _read("web/js/main.js")


# ── 新增的 i18n key（四語）────────────────────────────────────


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_new_i18n_keys(lang):
    d = json.loads(_read(f"web/js/i18n/{lang}.json"))
    assert d["modals"]["priceAlert"]["myAlerts"]
    assert d["journal"]["calendar"]["deleteConfirm"]
    assert d["toolSettings"]["confirmRemoveKey"]


# ── 6)／9) 治理頁與篩選器（頁面相依太多，靜態檢查）──────────────


def test_governance_submit_guard_and_422_detail():
    js = _read("web/governance/governance.js")
    assert "if (submitting) return;" in js
    assert "submitting = false;" in js
    # 422 的 detail 是陣列，toast 不能顯示 [object Object]
    assert "result.detail || window.I18n.t('governance.submitFailed')" not in js
    assert "result.detail || window.I18n.t('governance.voteFailed')" not in js
    assert js.count("typeof result.detail === 'string'") == 2


def test_screener_zero_change_and_missing_price():
    js = _read("web/js/market-screener.js")
    # 0.00% 不能標紅
    assert "val > 0 ? 'text-success' : val < 0 ? 'text-danger' : 'text-textMuted'" in js
    # 缺價不顯示 $NaN
    body = js[js.index("function formatPrice(price)") :]
    body = body[: body.index("\n}\n") + 3]
    assert "Number.isFinite(p)" in body
    _require_node()
    out = subprocess.run(
        [
            "node",
            "-e",
            body
            + "console.log(JSON.stringify([formatPrice(undefined), formatPrice('abc'), formatPrice(1234.5), formatPrice(0.5)]))",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["—", "—", "1,234.50", "0.5000"]
