"""Base Builder Code（ERC-8021）歸因尾巴——DANNY 2026-09-13：「Builder Code 也接上」。

USDC 付款是前端組 transfer calldata 送 eth_sendTransaction，尾巴接在 calldata 最後；
後端驗款讀 Transfer event（api/payment_rails.py），不看 calldata，所以不受影響。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent


def test_node_assertions_pass():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "builder_code.mjs")],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_premium_transfer_is_wrapped():
    # 送款本體 2026-09-25 抽到 usdc-pay.js（premium 與論壇共用），premium 委派過去
    premium = (REPO / "web" / "js" / "premium.js").read_text(encoding="utf-8")
    assert "return walletSendUsdc(order, bound);" in premium
    src = (REPO / "web" / "js" / "usdc-pay.js").read_text(encoding="utf-8")
    assert "import { withBuilderCode } from './builder-code.js';" in src
    assert re.search(
        r"const data = withBuilderCode\('0xa9059cbb' \+ padAddr\(order\.receiving_address\) \+ padAmount\(order\.micro\)\);",
        src,
    ), "USDC transfer calldata 要經過 withBuilderCode 才送出"


def test_backend_verifies_by_event_not_calldata():
    """守住前提：驗款只看 Transfer log，不解析 tx input（否則接尾巴會讓驗款失敗）。"""
    rails = (REPO / "api" / "payment_rails.py").read_text(encoding="utf-8")
    assert "ERC20_TRANSFER_TOPIC" in rails
    assert "a9059cbb" not in rails.lower()
    assert '["input"]' not in rails and "'input'" not in rails
