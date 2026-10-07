"""Node 端 JS 決策閘門（tests/js/premium_stable_rails.mjs）的 pytest 包裝。

與 tests/test_evm_wc_js_gates.py 同一模式：argv 全為字面常數、清單形式
執行；node 不存在時以 FileNotFoundError 分流為 skip——CI 有 node，本機
沒有也不該擋住整套測試。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _run_node_gate(script: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["node", script],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("node 不可用")


class TestPremiumStableRailsGate:
    def test_stable_rail_decisions(self):
        result = _run_node_gate("tests/js/premium_stable_rails.mjs")
        assert result.returncode == 0, (
            "premium 訂閱統一（USDC on Base）閘門行為不符預期：\n"
            + result.stdout
            + result.stderr
        )
        assert "premium stable rails tests passed" in result.stdout
