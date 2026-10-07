"""桌機側欄收合成圖示列（Teams 式，2026-09-29）。

- early-init.js 在第一次繪製前套 html.sidebar-collapsed（node 實跑）
- 收合樣式全部包在 min-width:768px 裡：手機抽屜不受影響
- SPA（GlobalNav）與獨立頁（site-sidebar.js）讀寫同一個 localStorage key，換頁狀態一致
行為（寬度、圖示列導覽可點、重整保持、手機抽屜照舊）由 Playwright 驗。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
KEY = "sidebarCollapsed"


def test_early_init_applies_before_first_paint_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "sidebar_collapse_early.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "sidebar_collapse_early: ok" in proc.stderr


def _media_blocks(css: str):
    """回傳 (media 條件, 區塊內容) —— 只處理一層 @media { ... }。"""
    out = []
    for m in re.finditer(r"@media([^{]+)\{", css):
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append((m.group(1), css[m.end() : i - 1], (m.start(), i)))
    return out


def test_collapsed_rules_only_apply_on_desktop():
    css = re.sub(
        r"/\*.*?\*/", "", (WEB / "styles.css").read_text(encoding="utf-8"), flags=re.S
    )
    blocks = _media_blocks(css)
    desktop_spans = [span for cond, _, span in blocks if "min-width: 768px" in cond]
    hits = [m.start() for m in re.finditer(r"html\.sidebar-collapsed", css)]
    assert hits, "錨點：styles.css 要有收合樣式"
    outside = [pos for pos in hits if not any(a <= pos < b for a, b in desktop_spans)]
    assert not outside, (
        "收合樣式要包在 @media (min-width: 768px)，否則手機抽屜會被縮成 64px"
    )


def test_spa_and_standalone_pages_share_one_key():
    for rel in ("js/global-nav.js", "js/site-sidebar.js", "js/early-init.js"):
        src = (WEB / rel).read_text(encoding="utf-8")
        assert f"'{KEY}'" in src, f"{rel} 要讀寫同一個 key（{KEY}），換頁收合狀態才一致"
