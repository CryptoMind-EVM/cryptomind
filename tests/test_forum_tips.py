"""論壇打賞：USDC on Base，直接付給作者自己的 EVM 地址（2026-09-25 DANNY）。

以前是 TON：作者要有 TON 身份或綁 TON 錢包，EVM 作者一律收不到。現在：

- 收款地址只能是作者的 EVM 身份地址（evm_0x… → 0x…）或 SIWE 綁定的 EVM 錢包，
  其他一律不行；作者都沒有 → 400＋清楚訊息（前端靠 author_tippable 先擋）。
- 金額 FORUM_TIP_MIN_USD～FORUM_TIP_MAX_USD（預設 0.1～100，沒帶用 1），後端驗。
- 領取時重查：收款地址還是作者現在的地址、付款人是打賞者綁定的錢包（fail-closed）。
- 防重放：tx hash 轉小寫寫進 tips.tx_hash（UNIQUE）。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.payment_rails as pr

ROOT = Path(__file__).resolve().parents[1]

AUTHOR_ADDR = "0x" + "d" * 40
EVM_AUTHOR = "evm_" + AUTHOR_ADDR
BOUND_EVM = "0x" + "b" * 40
TG_AUTHOR = "tg_123456"
TIPPER_ADDR = "0x" + "e" * 40
TIPPER = "evm_" + TIPPER_ADDR
STRANGER = "0x" + "c" * 40
PLATFORM = "0x" + "ab" * 20
TX = "0x" + "7a" * 32

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def evm_rail(monkeypatch):
    from api.middleware.rate_limit import limiter

    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", PLATFORM)
    monkeypatch.setattr(pr, "EVM_RPC_URL", "https://rpc.example")
    monkeypatch.setattr(pr, "EVM_CONFIRMATIONS", 8)
    limiter.reset()  # 同一支測試檔打同一個端點超過 10/min
    yield
    limiter.reset()


def _post(author, post_id=7):
    return {"id": post_id, "user_id": author, "is_hidden": False}


def _bound_map(author_bound, tipper_bound=(TIPPER_ADDR,)):
    """user_wallet_repo.bound_addresses(user_id, chain) 的替身。"""

    async def bound(user_id, chain, session=None):
        assert chain == "evm"
        if user_id == TIPPER:
            return list(tipper_bound)
        return list(author_bound)

    return bound


def _client(author, author_bound=(), tipper_bound=(TIPPER_ADDR,), headers=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.routers.forum.posts as posts_mod
    import api.routers.forum.tips as tips_mod
    from api.deps import get_current_user
    from core.orm.wallets_repo import user_wallet_repo

    app = FastAPI()
    app.state.limiter = tips_mod.limiter
    app.include_router(tips_mod.router)
    app.include_router(posts_mod.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": TIPPER}
    patches = [
        patch.object(
            tips_mod.forum_repo, "get_post_by_id", AsyncMock(return_value=_post(author))
        ),
        patch.object(
            user_wallet_repo,
            "bound_addresses",
            AsyncMock(side_effect=_bound_map(author_bound, tipper_bound)),
        ),
        patch.object(tips_mod, "TEST_MODE", False),
    ]
    return TestClient(app, headers=headers or {}), patches


def _call(
    author,
    method,
    path,
    json=None,
    author_bound=(),
    tipper_bound=(TIPPER_ADDR,),
    headers=None,
    extra_patches=(),
):
    client, patches = _client(author, author_bound, tipper_bound, headers)
    patches = list(patches) + list(extra_patches)
    for p in patches:
        p.start()
    try:
        return client.request(method, path, json=json)
    finally:
        for p in reversed(patches):
            p.stop()


def _tip_order(author=EVM_AUTHOR, **kw):
    return _call(
        author,
        "POST",
        "/api/forum/posts/7/tip/payment-order",
        json=kw.pop("json", {}),
        **kw,
    )


# --------------------------------------------------------------------------- #
# 作者收款地址                                                                  #
# --------------------------------------------------------------------------- #


class TestAuthorEvmAddresses:
    @pytest.mark.asyncio
    async def test_evm_identity_address(self):
        from api.routers.forum.payments import author_evm_addresses
        from core.orm.wallets_repo import user_wallet_repo

        with patch.object(
            user_wallet_repo, "bound_addresses", AsyncMock(return_value=[])
        ):
            assert await author_evm_addresses(EVM_AUTHOR) == [AUTHOR_ADDR]

    @pytest.mark.asyncio
    async def test_identity_plus_bound_deduped_and_lowercased(self):
        from api.routers.forum.payments import author_evm_addresses
        from core.orm.wallets_repo import user_wallet_repo

        bound = AsyncMock(
            return_value=[AUTHOR_ADDR.upper().replace("0X", "0x"), BOUND_EVM]
        )
        with patch.object(user_wallet_repo, "bound_addresses", bound):
            assert await author_evm_addresses(EVM_AUTHOR) == [AUTHOR_ADDR, BOUND_EVM]
        bound.assert_awaited_with(EVM_AUTHOR, "evm")

    @pytest.mark.asyncio
    async def test_non_evm_identity_uses_only_bound_wallets(self):
        from api.routers.forum.payments import author_evm_addresses
        from core.orm.wallets_repo import user_wallet_repo

        with patch.object(
            user_wallet_repo, "bound_addresses", AsyncMock(return_value=[BOUND_EVM])
        ):
            assert await author_evm_addresses(TG_AUTHOR) == [BOUND_EVM]
        with patch.object(
            user_wallet_repo, "bound_addresses", AsyncMock(return_value=[])
        ):
            assert await author_evm_addresses(TG_AUTHOR) == []

    @pytest.mark.asyncio
    async def test_invalid_and_platform_addresses_excluded(self):
        """格式不對的不收；平台收款地址不收（打賞的錢不能跟發文費／訂閱混到同一個收款人，
        不然同一筆轉帳能被兩邊各領一次）。"""
        from api.routers.forum.payments import author_evm_addresses
        from core.orm.wallets_repo import user_wallet_repo

        bound = AsyncMock(return_value=["0x123", "UQabc", PLATFORM, BOUND_EVM])
        with patch.object(user_wallet_repo, "bound_addresses", bound):
            assert await author_evm_addresses(TG_AUTHOR) == [BOUND_EVM]
        with patch.object(
            user_wallet_repo, "bound_addresses", AsyncMock(return_value=[])
        ):
            assert await author_evm_addresses("evm_" + PLATFORM) == []
            assert await author_evm_addresses("evm_0xnothex") == []

    @pytest.mark.asyncio
    async def test_repo_failure_keeps_only_identity(self):
        from api.routers.forum.payments import author_evm_addresses
        from core.orm.wallets_repo import user_wallet_repo

        boom = AsyncMock(side_effect=RuntimeError("db down"))
        with patch.object(user_wallet_repo, "bound_addresses", boom):
            assert await author_evm_addresses(EVM_AUTHOR) == [AUTHOR_ADDR]
            assert await author_evm_addresses(TG_AUTHOR) == []


# --------------------------------------------------------------------------- #
# 建單                                                                          #
# --------------------------------------------------------------------------- #


class TestTipOrder:
    def test_order_pays_author_identity_address_default_amount(self):
        from api.evm_verification import verify_multichain_order_token

        resp = _tip_order()
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["receiving_address"] == AUTHOR_ADDR
        assert data["asset"] == "USDC"
        assert data["fiat_amount_usd"] == 1.0
        assert 1_000_001 <= data["micro"] <= 1_004_999
        order = verify_multichain_order_token(data["order_token"], TIPPER)
        assert order["p"] == "forum_tip"
        assert order["recv"] == AUTHOR_ADDR
        assert order["post"] == 7

    def test_order_pays_bound_wallet_of_non_evm_author(self):
        resp = _tip_order(author=TG_AUTHOR, author_bound=(BOUND_EVM,))
        assert resp.status_code == 200, resp.text
        assert resp.json()["receiving_address"] == BOUND_EVM

    def test_author_without_evm_address_gets_clear_400(self):
        resp = _tip_order(author=TG_AUTHOR, author_bound=())
        assert resp.status_code == 400
        assert "not linked an EVM wallet" in resp.json()["detail"]

    @pytest.mark.parametrize("amount", [0.05, 100.01, 1000, 1.234])
    def test_amount_out_of_range_or_sub_cent_rejected(self, amount):
        resp = _tip_order(json={"amount": amount})
        assert resp.status_code in (400, 422), resp.text

    @pytest.mark.parametrize("amount", [0.1, 5, 100])
    def test_amount_within_range_accepted(self, amount):
        resp = _tip_order(json={"amount": amount})
        assert resp.status_code == 200, resp.text
        assert resp.json()["fiat_amount_usd"] == amount

    def test_range_follows_config(self, monkeypatch):
        import api.routers.forum.payments as fp

        monkeypatch.setattr(fp, "FORUM_TIP_MAX_USD", 5.0)
        assert _tip_order(json={"amount": 6}).status_code == 400
        assert _tip_order(json={"amount": 5}).status_code == 200

    def test_tipper_without_bound_wallet_rejected(self):
        resp = _tip_order(tipper_bound=())
        assert resp.status_code == 400
        assert "bind" in resp.json()["detail"].lower()

    def test_cannot_tip_own_post(self):
        resp = _tip_order(author=TIPPER)
        assert resp.status_code == 400

    def test_blocked_inside_telegram(self):
        resp = _tip_order(headers={"X-Platform": "tma"})
        assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 領取                                                                          #
# --------------------------------------------------------------------------- #


def _receipt_rpc(logs):
    receipt = MagicMock()
    receipt.json.return_value = {"result": {"status": "0x1", "logs": logs}}
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


def _log(micro, to=AUTHOR_ADDR, payer=TIPPER_ADDR):
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


def _order_token(
    user=TIPPER, plan="forum_tip", recv=AUTHOR_ADDR, post=7, micro=1_000_321, ttl=3600
):
    from api.evm_verification import create_multichain_order

    return create_multichain_order(
        user,
        plan,
        pr.RAIL_EVM_USDC,
        1.0,
        quoted_amount=micro / 1e6,
        ttl_seconds=ttl,
        extra={"micro": micro, "recv": recv, "post": post},
    )["order_token"]


def _claim(
    logs,
    order=None,
    tx=TX,
    author=EVM_AUTHOR,
    author_bound=(),
    tipper_bound=(TIPPER_ADDR,),
    create=None,
):
    import api.routers.forum.tips as tips_mod

    create = create or AsyncMock(return_value=11)
    resp = _call(
        author,
        "POST",
        "/api/forum/posts/7/tip",
        json={"order_token": order or _order_token(), "tx_hash": tx},
        author_bound=author_bound,
        tipper_bound=tipper_bound,
        extra_patches=(
            patch.object(pr.httpx, "AsyncClient", _receipt_rpc(logs)),
            patch.object(tips_mod.forum_repo, "create_tip", create),
        ),
    )
    return resp, create


class TestTipClaim:
    def test_valid_tip_recorded_with_paid_amount_and_lowercased_hash(self):
        resp, create = _claim([_log(1_000_321)], tx=TX.upper().replace("0X", "0x"))
        assert resp.status_code == 200, resp.text
        kw = create.await_args.kwargs
        assert kw["to_user_id"] == EVM_AUTHOR
        assert kw["from_user_id"] == TIPPER
        assert kw["tx_hash"] == TX
        assert kw["amount"] == pytest.approx(1.000321)

    def test_receiver_no_longer_author_address_rejected_before_rpc(self):
        """下單後作者解綁了那個錢包 → 領取時不認（不驗鏈、不記帳）。"""
        order = _order_token(recv=BOUND_EVM)
        resp, create = _claim(
            [_log(1_000_321, to=BOUND_EVM)],
            order=order,
            author=TG_AUTHOR,
            author_bound=(),
        )
        assert resp.status_code == 400
        assert "does not match post author" in resp.json()["detail"]
        create.assert_not_awaited()

    def test_payment_to_platform_instead_of_author_rejected(self):
        resp, create = _claim([_log(1_000_321, to=PLATFORM)])
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_wrong_amount_rejected(self):
        resp, create = _claim([_log(1_000_322)])
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_payer_must_be_bound_wallet(self):
        resp, create = _claim([_log(1_000_321, payer=STRANGER)])
        assert resp.status_code == 400
        assert "bound" in resp.json()["detail"].lower()
        create.assert_not_awaited()

    def test_tipper_without_bound_wallet_rejected(self):
        resp, create = _claim([_log(1_000_321)], tipper_bound=())
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_replay_rejected_with_409(self):
        from sqlalchemy.exc import IntegrityError

        seen = set()

        async def create_tip(**kwargs):
            if kwargs["tx_hash"] in seen:
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            seen.add(kwargs["tx_hash"])
            return len(seen)

        create = AsyncMock(side_effect=create_tip)
        order = _order_token()
        first, _ = _claim([_log(1_000_321)], order=order, create=create)
        assert first.status_code == 200, first.text
        again, _ = _claim(
            [_log(1_000_321)],
            order=order,
            tx=TX.upper().replace("0X", "0x"),
            create=create,
        )
        assert again.status_code == 409

    def test_order_for_another_post_rejected(self):
        resp, create = _claim([_log(1_000_321)], order=_order_token(post=8))
        assert resp.status_code == 400
        create.assert_not_awaited()

    @pytest.mark.parametrize("plan", ["forum_post", "premium_monthly"])
    def test_non_tip_order_rejected(self, plan):
        resp, create = _claim([_log(1_000_321)], order=_order_token(plan=plan))
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_expired_order_rejected(self):
        resp, create = _claim([_log(1_000_321)], order=_order_token(ttl=-5))
        assert resp.status_code == 400
        create.assert_not_awaited()

    def test_missing_order_token_rejected(self):
        resp = _call(EVM_AUTHOR, "POST", "/api/forum/posts/7/tip", json={"tx_hash": TX})
        assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# 文章詳情：只給布林，不外露地址                                                  #
# --------------------------------------------------------------------------- #


class TestPostDetailTippableFlag:
    def _detail(self, author, bound):
        post = {**_post(author), "payment_tx_hash": None}
        import api.routers.forum.posts as posts_mod

        resp = _call(
            author,
            "GET",
            "/api/forum/posts/7",
            author_bound=bound,
            extra_patches=(
                patch.object(
                    posts_mod.forum_repo, "get_post_by_id", AsyncMock(return_value=post)
                ),
            ),
        )
        assert resp.status_code == 200
        return resp.json()["post"]

    def test_evm_author_tippable(self):
        post = self._detail(EVM_AUTHOR, [])
        assert post["author_tippable"] is True
        assert "author_ton_tippable" not in post

    def test_author_with_bound_evm_wallet_tippable(self):
        assert self._detail(TG_AUTHOR, [BOUND_EVM])["author_tippable"] is True

    def test_author_without_evm_not_tippable(self):
        assert self._detail(TG_AUTHOR, [])["author_tippable"] is False

    def test_detail_does_not_expose_bound_address(self):
        post = self._detail(TG_AUTHOR, [BOUND_EVM])
        assert BOUND_EVM not in json.dumps(post)


# --------------------------------------------------------------------------- #
# TON 打賞整條移除                                                              #
# --------------------------------------------------------------------------- #


class TestTonTipRemoved:
    def test_no_ton_order_route_or_helpers(self):
        import api.routers.forum.tips as tips_mod

        paths = {r.path for r in tips_mod.router.routes}
        assert not any("ton-order" in p for p in paths)
        assert "/api/forum/posts/{post_id}/tip/payment-order" in paths
        assert not hasattr(tips_mod, "author_ton_addresses")
        assert not hasattr(tips_mod, "_record_tip_payment")

    def test_tip_request_has_no_ton_comment_field(self):
        from api.routers.forum.models import CreateTipRequest

        assert "comment" not in CreateTipRequest.model_fields


@pytest.mark.unit
class TestTipButtonGuardFrontend:
    """前端：作者不能收 USDC → 按打賞直接給在地化訊息，不開視窗、不建單。"""

    def test_handle_tip_checks_flag_before_order(self):
        src = (ROOT / "web/js/forum-app.js").read_text(encoding="utf-8")
        body = src[src.index("async handleTip(postId)") :]
        body = body[: body.index("// Create Post Logic")]
        guard = body.index("author_tippable === false")
        assert "forum.tipNoEvmWallet" in body[guard : guard + 400]
        assert guard < body.index("/tip/payment-order")
        assert "author_ton_tippable" not in src
        assert "executeForumTonPayment" not in src

    def test_tip_no_evm_wallet_key_in_all_locales(self):
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            data = json.loads(
                (ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            assert data["forum"].get("tipNoEvmWallet"), (
                f"{lang} 缺 forum.tipNoEvmWallet"
            )
            assert "tipNoTonWallet" not in data["forum"], f"{lang} 還留著 TON 版文案"
