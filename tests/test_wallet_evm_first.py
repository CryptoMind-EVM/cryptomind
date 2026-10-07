"""錢包分頁 EVM 優先（2026-09-12；設計 §3）：綁定錢包卡＋鏈上同步卡＋平台付款紀錄改名、
TON 鑽石圖示退場、click-delegator 接線、帳本「已驗證持倉」卡、四語、node 斷言。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _wallet_tab() -> str:
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    return html[html.index('<div id="wallet-tab"') : html.index("<!-- Tab: Trust")]


def test_wallet_tab_markup_is_evm_first():
    seg = _wallet_tab()
    for needle in (
        'id="wallet-bound-card"',
        'id="wallet-bound-list"',
        'id="wallet-holdings-total"',
        'id="wallet-sync-card"',
        'id="wallet-sync-enabled" type="checkbox"',
        'data-change-action="walletToggleSync"',
        'id="wallet-sync-now" data-click="walletSyncNow"',
        'data-click="GlobalNav.navigateToTab" data-click-arg="journal"',
        'id="wallet-sync-last"',
        'id="wallet-sync-result"',
        'data-i18n="wallet.myWallets"',
        'data-i18n="wallet.sync.title"',
        'data-i18n="wallet.transactions"',
        'id="wallet-total-out"',
        'id="wallet-total-in"',
        'id="wallet-tx-list"',
    ):
        assert needle in seg, needle
    # TON 鑽石 logo 與「Total Activity」卡退場；標題不再是 Wallet History
    assert "M12 2L21 12L12 22L3 12L12 2Z" not in seg
    assert 'data-i18n="wallet.totalActivity"' not in seg
    assert 'data-i18n="wallet.title">Wallet<' in seg
    # 三張卡的順序：我的錢包 → 鏈上同步 → 平台付款紀錄
    assert seg.index('id="wallet-bound-card"') < seg.index('id="wallet-sync-card"') < seg.index('id="wallet-tx-list"')
    assert seg.count("<div") == seg.count("</div>"), "wallet-tab 的 div 沒配平"


def test_click_delegator_routes_new_wallet_actions():
    src = (REPO / "web" / "js" / "click-delegator.js").read_text(encoding="utf-8")
    assert "if (action === 'walletSyncNow') { if (window.WalletApp) window.WalletApp.syncNow(); return; }" in src
    assert "walletCopyAddress" in src and "copyAddress(decodeDataValue(arg))" in src
    assert "if (action === 'walletToggleSync') { if (window.WalletApp) WalletApp.toggleSync(el); return; }" in src
    # 重新整理鈕要連錢包與同步狀態一起重載
    assert "if (action === 'walletLoad') { if (window.WalletApp) window.WalletApp.init(); return; }" in src


def test_wallet_js_loads_holdings_and_sync_together():
    src = (REPO / "web" / "js" / "wallet.js").read_text(encoding="utf-8")
    assert "Promise.all([this.loadData(), this.loadWallets(), this.loadSyncStatus()])" in src
    assert "AppAPI.get('/api/wallet/holdings')" in src
    assert "AppAPI.get('/api/journal/onchain/status')" in src
    assert "AppAPI.put('/api/journal/onchain/status'" in src
    assert "AppAPI.post('/api/journal/onchain/sync'" in src
    # 錢包卡內容全部經 _esc，地址進 data-click-arg 也要跳脫
    assert 'data-click-arg="${this._esc(w.address)}"' in src


def test_journal_verified_holdings_card():
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="journal-verified-holdings"' in html and 'id="journal-verified-holdings-list"' in html
    inv = html[html.index('id="journal-invest-view"') : html.index('id="journal-calendar"')]
    assert 'id="journal-positions-list"' in inv and 'id="journal-verified-holdings"' in inv, (
        "已驗證持倉卡要在持倉列表之後、行事曆之前"
    )
    js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(encoding="utf-8")
    assert "this._loadHoldings()" in js and "this._renderHoldings();" in js
    assert "holdings: null" in js


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales(lang):
    d = json.loads((REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    w = d["wallet"]
    for key in ("myWallets", "onchainTotal", "noWallets", "bindWallet", "primary", "copyAddress", "unverified", "balanceUnavailable"):
        assert key in w, f"{lang} 缺 wallet.{key}"
    for key in ("title", "hint", "auto", "now", "openLedger", "last", "never", "running", "failed", "tooSoon", "added", "duplicates", "unpriced", "errors", "rpcOnly"):
        assert key in w["sync"], f"{lang} 缺 wallet.sync.{key}"
    assert "{n}" in w["sync"]["added"] and "{list}" in w["sync"]["unpriced"]
    for key in ("title", "openWallet", "failed", "empty", "wallets", "none"):
        assert key in d["journal"]["holdings"], f"{lang} 缺 journal.holdings.{key}"
    assert w["title"] != "Wallet History"


def test_built_css_has_new_utilities():
    css = (REPO / "web" / "css" / "tailwind-built.css").read_text(encoding="utf-8")
    for cls in ("min-w-11", "min-h-9", "bg-surfaceHighlight\\/40"):
        assert f".{cls}" in css, f"tailwind-built.css 缺 {cls}（要跑 npm run build:css）"


def test_node_gate_passes():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "wallet_evm_first.mjs")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "wallet_evm_first: ok" in proc.stderr
