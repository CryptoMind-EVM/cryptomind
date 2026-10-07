"""點通知打開全文（2026-09-29）。

DANNY：「我點通知後什麼都沒有」。以前只有公告會開全文；早報、價格提醒、錢包監控點下去
只是關掉面板，列表又只顯示兩行，內容等於看不到。Base App 早報推播點開是 /?brief=日期，
前端也沒接。行為在 tests/js/notification_detail.mjs（node 實跑），這裡另外鎖前後端契約。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
DETAIL = REPO / "web" / "js" / "notification-detail.js"


def test_notification_detail_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "notification_detail.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "notification_detail: ok" in proc.stderr


def test_price_alert_markets_all_map_to_real_tabs():
    """後端加了市場、前端沒跟上 → 那個市場的提醒就沒有「查看行情」鈕（不會報錯，默默少）"""
    from core.database.price_alerts import VALID_MARKETS

    src = DETAIL.read_text(encoding="utf-8")
    block = re.search(r"const MARKET_TABS = \{(.*?)\};", src, re.S).group(1)
    mapping = dict(re.findall(r"(\w+): '([\w-]+)'", block))
    assert set(mapping) == set(VALID_MARKETS)

    spa = (REPO / "web" / "js" / "spa.js").read_text(encoding="utf-8")
    tabs = set(re.findall(r"'([\w-]+)'", re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S).group(1)))
    assert set(mapping.values()) <= tabs
    assert {"settings", "wallet-monitor"} <= tabs


def test_baseapp_push_target_matches_frontend_deep_link():
    """send_baseapp 的 targetUrl 格式要跟前端 briefDateFromSearch 認的一樣"""
    send = (REPO / "core" / "daily_brief" / "send.py").read_text(encoding="utf-8")
    assert 'f"/?brief={on_date}"' in send
    assert "get('brief')" in DETAIL.read_text(encoding="utf-8")
    service = (REPO / "web" / "js" / "notification-service.js").read_text(encoding="utf-8")
    assert "consumeBriefDeepLink(this.notifications," in service


def test_detail_modal_never_uses_innerhtml():
    """通知標題／內文可能含使用者自填的東西（早報的事件標題、分類），一律 textContent"""
    assert "innerHTML" not in DETAIL.read_text(encoding="utf-8")
