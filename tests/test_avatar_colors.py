"""彩色頭像（2026-10-01）。行為在 tests/js/avatar.mjs（node 實跑），這裡包成 pytest。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_avatar_node_gate():
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "avatar.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "avatar: ok" in proc.stderr


def test_profile_page_uses_avatar_colors():
    src = (REPO / "web/forum/js/profile-page.js").read_text(encoding="utf-8")
    assert "window.Avatar" in src, "個人頁頭像要走 avatar.js 的配色"
    html = (REPO / "web/forum/profile.html").read_text(encoding="utf-8")
    tag = html[html.index('id="public-profile-avatar"') :][:300]
    assert "bg-primary/20" not in tag and "text-primary" not in tag, "預設單色要拿掉，顏色交給 JS"
