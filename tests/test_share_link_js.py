"""`/?ask=` 分享連結前端（2026-10-05）。行為在 tests/js/share_link.mjs（node 實跑），這裡包成 pytest。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_share_link_node_gate():
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "share_link.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "share_link: ok" in proc.stderr
