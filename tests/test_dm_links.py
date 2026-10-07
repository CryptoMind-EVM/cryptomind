"""私訊連結與錢包地址（2026-09-29，PR 5）。切段、跳脫、punycode、自家網域在 tests/js/dm_links.mjs 實跑。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_links_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "dm_links.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_bubbles_render_through_link_tokenizer():
    """兩支氣泡渲染都要走 renderMessageText：只走 escape 的話連結不能點，
    自己再跑正則則是在已跳脫的 HTML 上切，會有 XSS 風險"""
    for path in ("web/js/messages.js", "web/js/friends.js"):
        lines = (REPO / path).read_text(encoding="utf-8").splitlines()
        code = [ln for ln in lines if not ln.lstrip().startswith(("//", "*", "/*"))]
        hits = [ln for ln in code if "msg-text" in ln and "renderMessageText(msg.content)" in ln]
        assert hits, f"{path} 的 .msg-text 沒有走 renderMessageText（註解裡的不算）"
        raw = [ln for ln in code if "msg-text" in ln and "scapeHtml(msg.content)" in ln]
        assert not raw, f"{path} 的 .msg-text 還在直接 escape 原文：{raw}"
