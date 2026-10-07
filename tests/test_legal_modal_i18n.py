"""條款／隱私／守則 modal 跟著目前語言走四語（2026-09-25 盤查）。

根因：web/js/legal.js 的 _applyDataLang 只分 zh-TW 與「其他」（一律 data-en），
zh-CN 與 ru 使用者打開 modal 看到英文——web/legal/*.html 明明每段都有
data-zh / data-zh-cn / data-en / data-ru 四語屬性（四語數量由
test_legal_i18n_parity.py 看守）。

行為測試在 tests/js/legal_modal_i18n.mjs（node 實跑 showLegalPage 與語言切換）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_legal_modal_follows_all_four_languages_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "legal_modal_i18n.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "legal_modal_i18n: ok" in proc.stderr


@pytest.mark.parametrize(
    "page", ["terms-of-service", "privacy-policy", "community-guidelines"]
)
def test_legal_pages_use_the_single_content_layout(page):
    """modal 的四語切換走 id="content"＋四語屬性這條路；舊的 content-zh／content-en
    雙區塊版型只分中英，頁面不能退回那種版型。"""
    html = (REPO / "web" / "legal" / f"{page}.html").read_text(encoding="utf-8")
    assert 'id="content"' in html
    assert 'id="content-zh"' not in html and 'id="content-en"' not in html
