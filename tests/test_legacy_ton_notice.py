"""舊 TON 身份提醒綁 EVM／Google（2026-09-25）＋錢包卡不再拿 user_id 當地址。

TON 登入已拔除：user_id 是 TON 地址的舊帳號只剩 refresh token 撐著，過期就再也
進不來。後端 /api/user/me 回 login_backup_needed（見 test_evm_first_identity），
前端顯示一次性、可關閉的橫幅。行為測試在 tests/js/legacy_ton_notice.mjs（node 實跑
legacy-ton-notice.js＋api-client.js＋auth.js），這裡包成 pytest 閘門＋標記檢查。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_notice_and_wallet_card_behaviour():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "legacy_ton_notice.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "legacy_ton_notice: ok" in proc.stderr


@pytest.fixture(scope="module")
def banner_html() -> str:
    src = (REPO / "web/index.html").read_text(encoding="utf-8")
    start = src.index('id="legacy-ton-banner"')
    return src[
        start : src.index("</div>", src.index("LegacyTonNotice.dismiss", start)) + 6
    ]


def test_banner_starts_hidden(banner_html):
    assert re.match(r'id="legacy-ton-banner"[^>]*class="hidden ', banner_html)


def test_banner_reuses_existing_bind_flows(banner_html):
    """綁定沿用既有流程：EVM 走 safeEvmBind（→ /api/user/wallets/bind），Google 走連接頁。"""
    assert 'data-click="safeEvmBind"' in banner_html
    assert (
        'data-click="GlobalNav.navigateToTab" data-click-arg="connections"'
        in banner_html
    )
    assert 'data-click="LegacyTonNotice.dismiss"' in banner_html


def test_banner_has_no_inline_handlers(banner_html):
    """正式站 CSP 擋 inline handler——只能用 click-delegator。"""
    assert not re.search(r"\son[a-z]+=", banner_html)


def test_delegator_allows_notice_root():
    src = (REPO / "web/js/click-delegator.js").read_text(encoding="utf-8")
    block = src[src.index("var allowedRoots = new Set([") :]
    block = block[: block.index("]);")]
    assert "'LegacyTonNotice'" in block


def test_notice_module_is_loaded_before_auth():
    """auth.js 的 _updateUI 發 auth:changed 時橫幅模組要已經在聽。"""
    src = (REPO / "web/js/main.js").read_text(encoding="utf-8")
    assert src.index("import './legacy-ton-notice.js'") < src.index(
        "import './auth.js'"
    )


def test_google_bind_announces_success():
    src = (REPO / "web/js/google-auth.js").read_text(encoding="utf-8")
    assert "google:linked" in src


@pytest.mark.parametrize("loc", ("zh-TW", "zh-CN", "en", "ru"))
def test_banner_copy_in_every_language(loc, banner_html):
    import json

    catalog = json.loads((REPO / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))
    for key in re.findall(r'data-i18n="([\w.]+)"', banner_html):
        node = catalog
        for part in key.split("."):
            node = node[part]
        assert isinstance(node, str) and node.strip(), f"{loc}:{key}"
