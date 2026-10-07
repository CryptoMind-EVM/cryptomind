"""USDC 訂單有效期＋tx hash 貼上（2026-09-12 DANNY 拍板）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_evm_order_ttl_is_at_least_seven_days():
    from api.routers import premium

    assert premium.EVM_ORDER_TTL_SECONDS >= 7 * 24 * 3600
    src = (REPO / "api/routers/premium.py").read_text(encoding="utf-8")
    body = src[
        src.index("async def create_payment_order") : src.index(
            "async def get_pricing_plans"
        )
    ]
    assert "ttl_seconds=EVM_ORDER_TTL_SECONDS" in body, (
        "建單要用 7 天 TTL，不是 TON 的 3 小時"
    )


def test_manual_block_has_tx_hash_input_wired_to_claim():
    js = (REPO / "web/js/premium.js").read_text(encoding="utf-8")
    assert 'id="stable-tx-hash"' in js
    handler = js[
        js.index("const paidBtn = overlay.querySelector('#stable-paid-btn')") :
    ]
    handler = handler[: handler.index("});", handler.index("runClaim(paidBtn"))]
    assert "#stable-tx-hash" in handler and "runClaim(paidBtn, raw || null)" in handler
    assert re.search(r"0x\[0-9a-fA-F\]\{64\}", handler), "貼錯格式要先擋，不要打 API"


def test_admin_modal_has_manual_settle_wired_to_endpoint():
    js = (REPO / "web/js/admin.js").read_text(encoding="utf-8")
    assert 'data-click="AdminPanel.UserManager.settleUsdc"' in js
    assert "/settle-usdc" in js
    py = (REPO / "api/routers/admin/users.py").read_text(encoding="utf-8")
    assert '@router.post("/users/{user_id}/settle-usdc")' in py
