"""付款前的退款同意（2026-09-28 DANNY）＋條款補上 support 信箱與退款申請方式。

付款後立即開通、開通後依條款 5.3 不退款——消保法「線上服務經消費者事先同意始提供」
要有事先同意：前端每次建單前確認一次，後端把 refund_ack 簽進訂單 token（隨訂單落庫）。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _premium_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.routers.premium as pm
    from api.deps import get_current_user

    app = FastAPI()
    app.include_router(pm.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u-refund"}
    return TestClient(app)


def _token_payload(order_token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(order_token.split(".")[0]))


@pytest.mark.parametrize("ack", [True, False])
def test_refund_ack_is_signed_into_the_order(monkeypatch, ack):
    import api.payment_rails as pr
    import api.routers.premium as pm

    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setattr(pr, "EVM_RPC_URL", "https://rpc.example")
    create = AsyncMock(return_value={})
    monkeypatch.setattr(pm.payment_order_repo, "create", create)
    body = {"plan": "premium_monthly"}
    if ack:
        body["refund_ack"] = True
    resp = _premium_client().post("/api/premium/payment-order", json=body)
    assert resp.status_code == 200, resp.text
    stored_token = create.call_args.kwargs["order_token"]
    assert _token_payload(stored_token)["refund_ack"] is ack, "同意與否要隨訂單落庫"


def test_frontend_asks_before_creating_the_order():
    js = (REPO / "web" / "js" / "premium.js").read_text(encoding="utf-8")
    handler = js.split("async handleUpgradeClick()", 1)[1].split("\n    }\n", 1)[0]
    assert handler.index("_confirmRefundPolicy()") < handler.index(
        "startStableRailUpgrade("
    ), "要先同意才建單"
    assert "refund_ack: true" in js
    for lang in ("en", "zh-TW", "zh-CN", "ru"):
        d = json.loads(
            (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
        )["premium"]
        for key in ("refundConsentTitle", "refundConsentMessage", "refundConsentAgree"):
            assert d[key], f"{lang} 缺 premium.{key}"
        assert "brief@getcryptomind.com" in d["refundConsentMessage"]


def test_legal_pages_list_support_email_and_refund_steps():
    tos = (REPO / "web" / "legal" / "terms-of-service.html").read_text(encoding="utf-8")
    refund = tos.split("5.3 不退款政策", 1)[1].split("6. 論壇使用", 1)[0]
    assert "brief@getcryptomind.com" in refund and "tx hash" in refund
    contact = tos.split("16. 聯絡我們", 1)[1]
    assert "brief@getcryptomind.com" in contact
    privacy = (REPO / "web" / "legal" / "privacy-policy.html").read_text(
        encoding="utf-8"
    )
    assert "brief@getcryptomind.com" in privacy.split("11. 聯絡我們", 1)[1]
    # 每種語言的版本都要帶到信箱（切語言時由 data-* 屬性換字）
    for attr in ("data-zh", "data-zh-cn", "data-en", "data-ru"):
        assert re.search(rf'{attr}="[^"]*brief@getcryptomind\.com', refund), attr
