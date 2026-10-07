"""論壇付費發文：免費會員只能走「簽章訂單＋鏈上驗證」，不接受自報的 tx hash。

盤查（2026-09-25）：免費會員送任何不以 mock_ 開頭的 payment_tx_hash 就能發文——
從沒上鏈驗證，只靠 UNIQUE index 擋重用。論壇雖 hidden，API 是開的。

2026-09-25 DANNY：發文費從 0.1 TON 改成 USDC on Base（FORUM_POST_FEE_USD，預設
0.5），付給平台收款地址，跟 premium 同一套：伺服器簽的訂單＋唯一金額尾數＋付款人
必須是自己綁定的錢包＋tx hash（轉小寫）寫進 UNIQUE 的 posts.payment_tx_hash。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.payment_rails as pr

pytestmark = pytest.mark.unit

FREE = {"user_id": "evm_0xfree", "username": "free"}
BODY = {
    "board_slug": "general",
    "category": "chat",
    "title": "hello",
    "content": "hello world content",
}
PLATFORM = "0x" + "ab" * 20
PAYER = "0x" + "cd" * 20
STRANGER = "0x" + "ef" * 20
TX = "0x" + "9f" * 32


@pytest.fixture(autouse=True)
def evm_rail(monkeypatch):
    import api.routers.forum.posts as posts
    from api.middleware.rate_limit import limiter

    # 這支測的是收費模式；預設 0（冷啟動免費）的行為在檔尾的 test_free_posting_*
    monkeypatch.setattr(posts, "FORUM_POST_FEE_USD", 0.5)
    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", PLATFORM)
    monkeypatch.setattr(pr, "EVM_RPC_URL", "https://rpc.example")
    monkeypatch.setattr(pr, "EVM_CONFIRMATIONS", 8)
    limiter.reset()  # 同一支測試檔打同一個端點超過 20/min
    yield
    limiter.reset()


def _receipt_rpc(logs, status="0x1"):
    """eth_getTransactionReceipt／eth_blockNumber（latest 0x5208）／eth_getBlockByNumber。"""
    receipt = MagicMock()
    receipt.json.return_value = {"result": {"status": status, "logs": logs}}
    head = MagicMock()
    head.json.return_value = {"result": "0x5208"}
    block = MagicMock()
    block.json.return_value = {"result": {"timestamp": "0x0"}}
    client = MagicMock()

    async def _post(url, json=None, **kwargs):
        method = (json or {}).get("method")
        return {"eth_blockNumber": head, "eth_getBlockByNumber": block}.get(
            method, receipt
        )

    client.post = _post
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _log(micro, to=PLATFORM, payer=PAYER):
    return {
        "address": pr.EVM_USDC_CONTRACT,
        "topics": [
            pr.ERC20_TRANSFER_TOPIC,
            pr._topic_for_address(payer),
            pr._topic_for_address(to),
        ],
        "data": hex(micro),
        "blockNumber": hex(0x5200),
    }


def _app(membership_premium: bool, bound, create):
    import api.routers.forum.posts as posts

    app = FastAPI()
    app.state.limiter = posts.limiter
    app.include_router(posts.router)

    async def fake_user():
        return FREE

    app.dependency_overrides[posts.get_current_user] = fake_user

    async def fake_run_sync(fn, *a, **k):
        if fn is posts.get_user_membership:
            return {"is_premium": membership_premium}
        if fn is posts.check_daily_post_limit:
            return {"allowed": True, "limit": 10}
        raise AssertionError(fn)

    from core.orm.wallets_repo import user_wallet_repo

    patches = [
        patch.object(posts, "run_sync", side_effect=fake_run_sync),
        patch.object(
            posts.forum_repo,
            "get_board_by_slug",
            new=AsyncMock(return_value={"id": 1, "is_active": True}),
        ),
        patch.object(posts.forum_repo, "create_post", new=create),
        patch.object(posts, "TEST_MODE", False),
        patch.object(
            user_wallet_repo, "bound_addresses", new=AsyncMock(return_value=bound)
        ),
    ]
    return TestClient(app), patches


def _run(membership_premium, bound, calls, create=None):
    """calls: list of (method, path, json, headers). Returns responses + create mock."""
    create = create or AsyncMock(return_value={"success": True, "post_id": 1})
    client, patches = _app(membership_premium, bound, create)
    for p in patches:
        p.start()
    try:
        out = []
        for method, path, json, headers in calls:
            out.append(client.request(method, path, json=json, headers=headers or {}))
        return out, create
    finally:
        for p in patches:
            p.stop()


def _post(premium: bool, extra: dict, bound=(PAYER,)):
    (resp,), create = _run(
        premium, list(bound), [("POST", "/api/forum/posts", {**BODY, **extra}, None)]
    )
    return resp, create


def _order(user_id=FREE["user_id"], plan="forum_post", micro=500_123, ttl=3600):
    from api.evm_verification import create_multichain_order

    return create_multichain_order(
        user_id,
        plan,
        pr.RAIL_EVM_USDC,
        0.5,
        quoted_amount=micro / 1e6,
        ttl_seconds=ttl,
        extra={"micro": micro, "recv": PLATFORM},
    )["order_token"]


# --------------------------------------------------------------------------- #
# #873：自報 hash 一律 402                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fake_hash", ["0x" + "ab" * 32, "anything", "tx_123"])
def test_free_member_self_reported_tx_hash_is_rejected(fake_hash):
    resp, create = _post(False, {"payment_tx_hash": fake_hash})
    assert resp.status_code == 402
    create.assert_not_awaited()


def test_free_member_without_payment_is_rejected():
    resp, create = _post(False, {})
    assert resp.status_code == 402
    create.assert_not_awaited()


def test_premium_member_client_hash_is_not_stored():
    """Premium 不用付費；客戶端塞的 hash 不能寫進 UNIQUE 欄位（能拿來佔用別人的 tx）。"""
    resp, create = _post(True, {"payment_tx_hash": "0x" + "cd" * 32})
    assert resp.status_code in (200, 201)
    assert create.await_args.kwargs["payment_tx_hash"] is None


def test_premium_member_order_token_is_ignored():
    """Premium 帶了訂單也不驗、不收——免費發文，hash 不落庫。"""
    with patch.object(pr.httpx, "AsyncClient", MagicMock(side_effect=AssertionError)):
        resp, create = _post(True, {"order_token": _order(), "payment_tx_hash": TX})
    assert resp.status_code == 200
    assert create.await_args.kwargs["payment_tx_hash"] is None


# --------------------------------------------------------------------------- #
# 建單：/api/forum/posts/payment-order                                         #
# --------------------------------------------------------------------------- #


def _create_order(premium=False, bound=(PAYER,), headers=None):
    (resp,), _ = _run(
        premium,
        list(bound),
        [("POST", "/api/forum/posts/payment-order", {}, headers)],
    )
    return resp


def test_payment_order_is_server_signed_usdc_to_platform():
    from api.evm_verification import verify_multichain_order_token

    resp = _create_order()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["asset"] == "USDC"
    assert data["receiving_address"] == PLATFORM
    assert data["token_contract"] == pr.EVM_USDC_CONTRACT
    assert data["fiat_amount_usd"] == 0.5
    assert 500_001 <= data["micro"] <= 504_999  # 0.5 USD + 唯一尾數
    order = verify_multichain_order_token(data["order_token"], FREE["user_id"])
    assert order["p"] == "forum_post"
    assert order["rail"] == "evm_usdc"
    assert order["micro"] == data["micro"]


def test_payment_order_fee_follows_config(monkeypatch):
    import api.routers.forum.posts as posts

    monkeypatch.setattr(posts, "FORUM_POST_FEE_USD", 2.0)
    data = _create_order().json()
    assert data["fiat_amount_usd"] == 2.0
    assert 2_000_001 <= data["micro"] <= 2_004_999


def test_payment_order_rejects_premium_member():
    resp = _create_order(premium=True)
    assert resp.status_code == 400


def test_payment_order_requires_bound_wallet():
    resp = _create_order(bound=())
    assert resp.status_code == 400
    assert "bind" in resp.json()["detail"].lower()


def test_payment_order_blocked_inside_telegram():
    resp = _create_order(headers={"X-Platform": "tma"})
    assert resp.status_code == 403


def test_payment_order_unavailable_without_platform_address(monkeypatch):
    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", "")
    assert _create_order().status_code == 503


# --------------------------------------------------------------------------- #
# 領取：帶訂單＋tx hash 發文                                                    #
# --------------------------------------------------------------------------- #


def _claim(logs, tx=TX, order=None, bound=(PAYER,), create=None):
    order = order or _order()
    with patch.object(pr.httpx, "AsyncClient", _receipt_rpc(logs)):
        (resp,), create = _run(
            False,
            list(bound),
            [
                (
                    "POST",
                    "/api/forum/posts",
                    {**BODY, "order_token": order, "payment_tx_hash": tx},
                    None,
                )
            ],
            create=create,
        )
    return resp, create


def test_valid_usdc_tx_creates_post_with_lowercased_hash():
    resp, create = _claim([_log(500_123)], tx=TX.upper().replace("0X", "0x"))
    assert resp.status_code == 200, resp.text
    assert create.await_args.kwargs["payment_tx_hash"] == TX  # 小寫才能讓 UNIQUE 擋重放


def test_wrong_amount_rejected():
    resp, create = _claim([_log(500_124)])
    assert resp.status_code == 400
    assert "mismatch" in resp.json()["detail"].lower()
    create.assert_not_awaited()


def test_payment_to_wrong_receiver_rejected():
    resp, create = _claim([_log(500_123, to=STRANGER)])
    assert resp.status_code == 400
    create.assert_not_awaited()


def test_payment_from_unbound_wallet_rejected():
    resp, create = _claim([_log(500_123, payer=STRANGER)])
    assert resp.status_code == 400
    assert "bound" in resp.json()["detail"].lower()
    create.assert_not_awaited()


def test_account_without_bound_wallet_rejected_before_rpc():
    with patch.object(pr.httpx, "AsyncClient", MagicMock(side_effect=AssertionError)):
        (resp,), create = _run(
            False,
            [],
            [
                (
                    "POST",
                    "/api/forum/posts",
                    {**BODY, "order_token": _order(), "payment_tx_hash": TX},
                    None,
                )
            ],
        )
    assert resp.status_code == 400
    create.assert_not_awaited()


def test_replayed_tx_rejected_with_409():
    """同一筆 tx（含換大小寫）第二次發文：UNIQUE 撞到 → 409，不是 500。"""
    from sqlalchemy.exc import IntegrityError

    seen = set()

    async def create_post(**kwargs):
        h = kwargs["payment_tx_hash"]
        if h in seen:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        seen.add(h)
        return {"success": True, "post_id": len(seen)}

    create = AsyncMock(side_effect=create_post)
    order = _order()
    first, _ = _claim([_log(500_123)], order=order, create=create)
    assert first.status_code == 200, first.text
    again, _ = _claim(
        [_log(500_123)], tx=TX.upper().replace("0X", "0x"), order=order, create=create
    )
    assert again.status_code == 409
    assert "already been used" in again.json()["detail"]


def test_expired_order_rejected():
    resp, create = _claim([_log(500_123)], order=_order(ttl=-10))
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()
    create.assert_not_awaited()


def test_order_of_another_user_rejected():
    resp, create = _claim([_log(500_123)], order=_order(user_id="evm_0xsomeoneelse"))
    assert resp.status_code == 403
    create.assert_not_awaited()


@pytest.mark.parametrize("plan", ["premium_monthly", "forum_tip"])
def test_non_post_order_rejected(plan):
    """premium／打賞的訂單不能拿來發文（金額與收款人都不一樣）。"""
    resp, create = _claim([_log(500_123)], order=_order(plan=plan))
    assert resp.status_code == 400
    create.assert_not_awaited()


def test_scan_path_when_no_tx_hash():
    """沒帶 tx hash（例如錢包沒回傳）→ 走 getLogs 掃描，不是直接放行。"""
    import api.routers.forum.payments as fp

    scan = AsyncMock(return_value={"tx_hash": TX.upper().replace("0X", "0x")})
    with patch.object(fp.payment_rails, "verify_evm_usdc_payment", scan):
        (resp,), create = _run(
            False,
            [PAYER],
            [("POST", "/api/forum/posts", {**BODY, "order_token": _order()}, None)],
        )
    assert resp.status_code == 200, resp.text
    assert scan.await_args.args[0] == 500_123
    assert scan.await_args.kwargs["payer_addresses"] == [PAYER]
    assert "receiving_address" not in scan.await_args.kwargs  # 平台預設
    assert create.await_args.kwargs["payment_tx_hash"] == TX


def test_old_ton_order_endpoint_removed():
    import api.routers.forum.posts as posts

    paths = {r.path for r in posts.router.routes}
    assert "/api/forum/posts/ton-order" not in paths
    assert "/api/forum/posts/payment-order" in paths
    assert not hasattr(posts, "create_post_ton_order")


# --------------------------------------------------------------------------- #
# 冷啟動免費發文（2026-09-25 DANNY）：FORUM_POST_FEE_USD 預設 0                  #
# --------------------------------------------------------------------------- #


def test_free_posting_is_the_default():
    import importlib

    import core.config as cfg

    with patch.dict("os.environ", {}, clear=False) as env:
        env.pop("FORUM_POST_FEE_USD", None)
        try:
            assert importlib.reload(cfg).FORUM_POST_FEE_USD == 0
        finally:
            importlib.reload(cfg)


@pytest.mark.parametrize("fake_hash", [None, "0x" + "ab" * 32])
def test_free_posting_lets_free_member_post_without_payment(monkeypatch, fake_hash):
    import api.routers.forum.posts as posts

    monkeypatch.setattr(posts, "FORUM_POST_FEE_USD", 0.0)
    extra = {"payment_tx_hash": fake_hash} if fake_hash else {}
    resp, create = _post(False, extra, bound=())
    assert resp.status_code == 200, resp.text
    # 自報的 hash 不能寫進 UNIQUE 欄位（否則能佔用別人的 tx）
    assert create.await_args.kwargs["payment_tx_hash"] is None


def test_free_posting_payment_order_is_rejected(monkeypatch):
    import api.routers.forum.posts as posts

    monkeypatch.setattr(posts, "FORUM_POST_FEE_USD", 0.0)
    (resp,), _ = _run(
        False, [PAYER], [("POST", "/api/forum/posts/payment-order", {}, None)]
    )
    assert resp.status_code == 400
    assert "free" in resp.json()["detail"].lower()
