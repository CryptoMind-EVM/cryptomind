import asyncio
from typing import Literal, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address

from api.deps import (
    clear_token_cookies,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_optional_current_user,
    set_token_cookies,
)
from api.evm_verification import (
    issue_siwe_challenge,
    recover_siwe_signer_async,
    resolve_siwe_domain,
    verify_siwe_wallet_message_async,
)
from api.funnel import record_guest_converted
from api.middleware.rate_limit import limiter
from api.models import AnalysisPreferenceInput
from api.utils import logger, run_sync
from core.audit import audit_log
from core.config import (
    MULTICHAIN_ENABLED,
    TEST_MODE,
    TEST_USER,
    TRUST_EVM_BINDING_ENABLED,
    TRUST_SCORE_ENABLED,
)
from core.database import (
    create_or_get_user,
    get_user_wallet_status,
    save_user_feedback,
)
from core.database.user import (
    SYSTEM_USERNAME_PREFIXES,
    get_user_by_id,
    get_user_evm_address,
    is_display_name_taken,
    is_username_taken,
    set_user_display_name,
    set_user_evm_address,
    upgrade_to_pro,
)
from core.onchain.addresses import is_ton_address
from core.orm.repositories import user_repo
from core.orm.user_api_keys_repo import LLM_PROVIDERS
from core.orm.user_llm_preferences_repo import user_llm_preferences_repo
from core.orm.wallets_repo import CHAIN_EVM, user_wallet_repo

router = APIRouter()


# --- Dev/Test Login Endpoint ---


class DevLoginRequest(BaseModel):
    user_id: Optional[str] = None
    confirmation: str = Field(
        "I_UNDERSTAND_THE_RISKS", description='Must be "I_UNDERSTAND_THE_RISKS"'
    )

    def model_post_init(self, __context):
        if self.confirmation != "I_UNDERSTAND_THE_RISKS":
            raise ValueError('confirmation must be "I_UNDERSTAND_THE_RISKS"')


class UserFeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    language: str = Field(default="zh-TW")


@router.post("/api/user/dev-login")
@limiter.limit("5/minute")
async def dev_login(request: Request, response: Response, body: DevLoginRequest = None):
    """
    僅在 TEST_MODE=True 時可用的開發測試登入
    返回測試用戶的 JWT Token
    可選：傳入 user_id 以切換到特定測試用戶
    """
    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="Test mode is disabled")

    if body and body.user_id:
        test_user_id = body.user_id
        suffix = (
            test_user_id.split("-")[-1] if "-" in test_user_id else test_user_id[-3:]
        )
        test_username = f"TestUser_{suffix}"
    else:
        test_user_id = TEST_USER.get("uid", "test-user-001")
        test_username = TEST_USER.get("username", "TestUser")

    access_token = create_access_token(
        data={"sub": test_user_id, "username": test_username}
    )
    refresh_token = create_refresh_token(
        data={"sub": test_user_id, "username": test_username}
    )

    try:
        existing_user = await run_sync(get_user_by_id, test_user_id)
        if not existing_user:
            await run_sync(create_or_get_user, test_user_id, test_username, "dev_test")
            logger.info(
                f"[DEV LOGIN] Created missing mock user {test_username} ({test_user_id}) in DB."
            )

        # Test accounts default to Premium so the full feature set (custom
        # System Prompt, tool toggles, etc.) is exercisable without manually
        # picking a special user id. TEST_MODE only — never reached in prod.
        await run_sync(upgrade_to_pro, test_user_id, 12, None)

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[DEV LOGIN] Error ensuring test user exists: {e}")

    set_token_cookies(response, access_token, refresh_token)

    return {
        "success": True,
        "user": {
            "uid": test_user_id,
            "username": test_username,
            "authMethod": "dev_test",
        },
    }


# --- TON 錢包登入端點已移除（2026-09-08）---
#
# 登入 2026-08-31 起統一走 EVM（SIWE）。前端的 TON 登入鏈在 #681 刪除後，
# POST /api/user/ton-login 與 GET /api/user/ton-proof-payload 就再也沒有任何
# 呼叫端——留著等於一個沒人用、卻能直接發 JWT cookie 的認證入口。
#
# api/ton_verification.py 當時留著給 TON 付款用；2026-09-26 付款全部改成 Base USDC、
# 已無呼叫端，整支移除（網域判斷早已搬到 api/public_base.expected_site_domain）。
#
# ⚠️ DB 裡既有 auth_method='ton_wallet' 的帳號不受影響，也沒有被刪除
#    （admin/stats.py 仍在統計它們）。那些人自 2026-08-31 前端入口移除起就
#    已經無法用 TON 登入，這次移除沒有讓任何人多被鎖在外面。


def _enforce_auth_lockout(client_ip: str) -> None:
    """該 IP 失敗太多次就擋下來（``AUTH_LOCKOUT_ENABLED`` 開才生效）。

    2026-09-06：這個旗標在這之前是 phantom——``is_locked_out()`` 寫好了、
    測試也有，但 production code 一處都沒有呼叫，所以把
    ``AUTH_LOCKOUT_ENABLED=true`` 設下去唯一的效果是讓一個沒人問的函式
    改變回傳值。這裡把線接上。

    回 429 而不是 401/403：讓客戶端知道「是頻率問題，等一下會好」而不是
    「憑證錯了」，錢包 App 才不會引導使用者去重新綁定。
    """
    from core.auth_failure_tracker import is_locked_out

    if not client_ip or not is_locked_out(client_ip):
        return
    from core.auth_failure_tracker import _window_seconds

    retry_after = _window_seconds()
    logger.warning("[AuthLockout] 擋下 %s（失敗次數超過阻擋閾值）", client_ip)
    raise HTTPException(
        status_code=429,
        detail="登入嘗試過於頻繁，請稍後再試。",
        headers={"Retry-After": str(retry_after)},
    )


def _clear_auth_failures(client_ip: str) -> None:
    """登入成功 → 清掉該 IP 的失敗記錄。

    ⚠️ 這會連同「同一個出口 IP 上其他人的失敗」一起清掉，對攻擊者是放寬。
    但這個站的使用者大量在電信商 NAT 與 Telegram Mini App 後面共用 IP，
    不清的話一個人簽章失敗幾次就會累積到把整個出口的人一起鎖住。
    在「誤鎖真實使用者」與「攻擊者蹭到重置」之間，這裡選前者。
    """
    if not client_ip:
        return
    try:
        from core.auth_failure_tracker import reset_failures

        reset_failures(client_ip)
    except Exception:  # pragma: no cover - 清計數失敗不該擋住已經成功的登入
        pass


# ===========================================================================
# EVM (SIWE) 登入與錢包綁定（c041，multichain design Part B）
# ===========================================================================


def _require_multichain() -> None:
    """Rollback switch: 503 when MULTICHAIN_ENABLED=false (design §風險與回滾)."""
    if not MULTICHAIN_ENABLED:
        raise HTTPException(status_code=503, detail="Multichain login is disabled")


def _sync_trust_evm_binding(user_id: str, addr: str) -> None:
    """登入簽章已證明 addr 所有權——Human Passport 綁定庫跟著補上。

    業界標準（Galxe 模型，ERC-4361 生態）：登入錢包在登入當下驗證，
    之後「附加」的錢包才需要獨立簽章。此前 EVM 登入者要在 #trust 對
    同一個錢包再簽一次名，純屬冗餘——2026-09-08 登入統一 EVM 後每個
    使用者都會撞到。

    只在 users.evm_address 為空時寫入：已有值＝使用者手動綁了其他地址，
    不覆蓋。任何失敗（unique 衝突／欄位未 migrate）只記 log——
    attestation 絕不擋登入；分數由 /api/trust/score 的漂移偵測惰性重算。
    """
    try:
        if get_user_evm_address(user_id):
            return
        ok, reason = set_user_evm_address(user_id, addr)
        if not ok:
            logger.warning(
                "evm login: trust autobind skipped for %s (%s)", user_id, reason
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("evm login: trust autobind failed for %s: %s", user_id, exc)


async def _record_embedded_login(user_id: str, address: str, provider: str) -> None:
    """Email／Google 登入記一筆給後台看（c064）；統計用，失敗絕不擋登入。"""
    from core import embedded_wallet_users

    try:
        await run_sync(
            lambda: embedded_wallet_users.record_login(user_id, address, provider)
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("evm login: embedded wallet record failed for %s: %s", user_id, exc)


@router.get("/api/user/evm-nonce")
# One-Click Auth 一次連線會取兩顆 nonce（AppKit 先問一次組訊息、authenticate
# 再問一次），10/分鐘等於使用者重試 5 次就被擋——正好在登入失敗、最想重試的
# 時候再補一刀。端點本身是無狀態 HMAC、不碰 DB，放寬到 20 沒有額外成本。
@limiter.limit("20/minute")
async def get_evm_nonce(request: Request, address: str | None = None):
    """Issue a SIWE challenge: nonce payload + the exact message to personal_sign.

    ``address`` is optional — One-Click Auth needs a nonce before the wallet is
    approved, so it asks without one and the wallet composes the message.
    """
    _require_multichain()
    challenge = issue_siwe_challenge(
        address, domain=resolve_siwe_domain(request.headers.get("host"))
    )
    return {"success": True, **challenge}


class EvmLoginRequest(BaseModel):
    address: str
    signature: str = Field(min_length=1)
    # 舊路徑（注入錢包／桌機）：後端用 nonce_token 重建自己發的訊息比對
    nonce_token: str | None = Field(default=None, min_length=1)
    # One-Click Auth：ERC-4361 訊息由錢包自己組，後端只能驗使用者送上來的這份
    # （信任錨是訊息裡那顆我們簽的 nonce）
    message: str | None = Field(default=None, min_length=1, max_length=4096)
    # 用什麼登入（前端回報，只拿來統計）：wallet＝外部錢包；email／google／social＝Reown 內嵌錢包
    # （2026-09-30 後台要看 Email／Google 人數，對照 Reown 免費方案每月 500 人上限）
    login_via: Literal["wallet", "email", "google", "social"] | None = None


@router.post("/api/user/evm-login")
@limiter.limit("20/minute")
async def evm_login(request: Request, response: Response, body: EvmLoginRequest):
    """
    Authenticate an EVM wallet via SIWE (personal_sign + eth_account recover).

    Identity is deterministic: ``evm_<lower-case address>`` (mirrors ton-login's
    address-as-identity). The user_wallets row is registered on first login;
    a UNIQUE(chain, address) conflict means the address was already bound
    (cross-chain bind case) — we proceed with the deterministic identity and
    log the conflict. Fail-closed: no path lets a signature reach another
    user's account.
    """
    from core.auth_failure_tracker import record_auth_failure

    _require_multichain()

    client_ip = get_remote_address(request)
    _enforce_auth_lockout(client_ip)
    if not body.message and not body.nonce_token:
        raise HTTPException(status_code=400, detail="message or nonce_token required")
    # domain 跟 evm-nonce 同一個來源（請求 Host）：換了 host 就對不上，401
    siwe_domain = resolve_siwe_domain(request.headers.get("host"))
    try:
        if body.message:
            addr = await verify_siwe_wallet_message_async(
                address=body.address,
                message=body.message,
                signature=body.signature,
                expected_domain=siwe_domain,
            )
        else:
            addr = await recover_siwe_signer_async(
                address=body.address,
                payload=body.nonce_token,
                signature=body.signature,
                domain=siwe_domain,
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except HTTPException as proof_err:
        # 只有「簽章層」的失敗才算暴力嘗試。400 格式錯與「nonce 過期」
        # （10 分鐘 TTL，手機錢包來回很容易超過）是 UX 事件——計進去的話
        # 單一挫敗使用者自己就能把共享 IP 累到鎖定閾值（2026-09-10 徹查）。
        if (
            proof_err.status_code == 401
            and "expired" not in str(proof_err.detail).lower()
        ):
            record_auth_failure(client_ip, f"evm siwe invalid: {proof_err.status_code}")
        raise
    except ValueError as sig_err:
        record_auth_failure(client_ip, "evm siwe invalid: malformed signature")
        logger.warning("SIWE signature decode failed: %s", sig_err)
        raise HTTPException(status_code=401, detail="Invalid wallet signature")

    # 合約錢包（Coinbase Smart Wallet／Safe）自 2026-09-12 起由 EIP-1271／6492 驗章涵蓋

    # 跨鏈歸戶：地址若已綁定其他帳號（例如 TON 用戶綁了這個 EVM 地址），
    # 登入該帳號而非建立新的 evm_ 帳號（design §B 身份解析規則 1）
    binding = await user_wallet_repo.get_binding(CHAIN_EVM, addr)
    try:
        if binding:
            result = await run_sync(
                lambda: create_or_get_user(identity=binding["user_id"])
            )
        else:
            identity = f"evm_{addr}"
            username = "EVM_" + addr[2:8]
            result = await run_sync(
                lambda: create_or_get_user(
                    identity=identity, username=username, auth_method="evm_wallet"
                )
            )
    except ValueError as e:
        logger.warning("EVM 用戶同步失敗 - 用戶名衝突: %s", e)
        raise HTTPException(status_code=409, detail=str(e))

    try:
        await user_wallet_repo.register_binding(
            result["user_id"], CHAIN_EVM, addr, is_primary=True
        )
    except ValueError:
        # 同帳號重登（冪等）；跨鏈衝突已在上方 get_binding 歸戶，理論不達
        logger.info(
            "evm login: binding row conflict for %s:%s — proceeding with "
            "resolved identity",
            CHAIN_EVM,
            addr[:10],
        )

    # Human Passport：這份簽章已證明地址所有權，綁定庫自動補上（失敗不擋登入；
    # 旗標關閉時跳過——回滾路徑與 /api/trust/evm/bind 一致）
    if TRUST_EVM_BINDING_ENABLED and TRUST_SCORE_ENABLED:
        await run_sync(lambda: _sync_trust_evm_binding(result["user_id"], addr))

    access_token = create_access_token(
        data={"sub": result["user_id"], "username": result["username"]}
    )
    refresh_token = create_refresh_token(
        data={"sub": result["user_id"], "username": result["username"]}
    )
    set_token_cookies(response, access_token, refresh_token)

    audit_log(
        action="wallet_connected",
        user_id=result["user_id"],
        username=result["username"],
        metadata={
            "wallet_address": addr,
            "chain": CHAIN_EVM,
            "is_new_user": result.get("is_new", False),
            "ip": client_ip,
            "login_via": body.login_via,
        },
    )
    if body.login_via in ("email", "google", "social"):
        await _record_embedded_login(result["user_id"], addr, body.login_via)
    # 轉換漏斗（PR-5）：帶著訪客 cookie 登入 → 記下訪客與帳號的對應
    record_guest_converted(
        request, result["user_id"], "evm", result.get("is_new", False)
    )

    _clear_auth_failures(client_ip)
    return {
        "success": True,
        "user": {
            "user_id": result["user_id"],
            "username": result["username"],
            "auth_method": result.get("auth_method", "evm_wallet"),
            "role": result.get("role", "user"),
            "membership_tier": result.get("membership_tier", "free"),
            "has_wallet": True,
            "wallet_address": addr,
        },
        "is_new_user": result.get("is_new", False),
    }


class WcConnectEventRequest(BaseModel):
    """前端 WalletConnect 連線遙測（2026-09-05 三種無聲卡死的回報）。"""

    mode: Literal[
        "pairing-dead",  # 死 QR：proposal 從未發佈，錢包端永遠不會有簽署畫面
        "cancel-close",  # 彈窗關閉且未連線（使用者取消）
        "connected-stalled",  # 已連線但 provider 遲遲不可讀
        "sign-timeout",  # personal_sign 逾時（簽署畫面多半沒在錢包端顯示）
        "sign-resent",  # 使用者切回頁面→立即重發（錢包凍結漏包對策）
        "sign-hex-fallback",  # hex personal_sign 被錢包拒收，退回純字串重簽
        "login-cancelled",  # 登入以「使用者拒絕」收場（錢包可能根本沒給簽署鈕）
        "login-failed",  # 登入以其他錯誤收場（summary 帶錢包錯誤碼／訊息）
        "social-stalled",  # Email／Google 登入彈窗卡死，被前端看門狗收掉（summary 帶卡在哪一步）
    ]
    summary: str = Field(default="", max_length=300)


@router.post("/api/user/wc-connect-event")
@limiter.limit("10/minute")
async def wc_connect_event(request: Request, body: WcConnectEventRequest) -> dict:
    """記一行可 grep 的 [WcConnect]——匿名、純記錄、不含任何個人資料。

    EVM 掃碼登入的無聲失敗都發生在前端（瀏覽器↔relay），後端原本一片空白
    （2026-09-05 線上實測：第一次掃描連 evm-nonce 都沒發出，事後只能靠
    ?wc-debug=1 重現取證）。有這行，事後判讀直接翻 log。
    """
    # 與 /api/client/log 同款消毒：匿名端點不得能偽造多行 log 誤導取證
    safe_summary = str(body.summary).replace("\r", " ").replace("\n", " ")
    logger.info("[WcConnect] mode=%s summary=%s", body.mode, safe_summary)
    return {"success": True}


class WalletBindRequest(BaseModel):
    chain: Literal["evm"] = "evm"  # MVP: EVM 綁定（TON 綁定第二批）
    address: str
    signature: str = Field(min_length=1)
    nonce_token: str = Field(min_length=1)


@router.get("/api/user/wallets")
async def list_wallets(current_user: dict = Depends(get_current_user)):
    """目前帳號已綁定的錢包地址。

    付款驗證（/api/premium/upgrade）只認從已綁定地址送出的 USDC；前端在
    建單前先查這份清單，沒綁定就先引導綁定、錢包直付前比對付款帳號
    （2026-09-11 盤查：以前錢先轉出去，claim 階段才被擋）。
    """
    user_id = current_user.get("user_id")
    try:
        rows = await user_wallet_repo.list_for_user(user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("wallet list failed for %s: %s", user_id, exc)
        rows = []
    return {
        "success": True,
        "wallets": rows,
        "evm_addresses": [
            r["address"]
            for r in rows
            if r.get("chain") == CHAIN_EVM and r.get("address")
        ],
    }


@router.post("/api/user/wallets/bind")
@limiter.limit("5/minute")
async def bind_wallet(
    request: Request,
    body: WalletBindRequest,
    current_user: dict = Depends(get_current_user),
):
    """Bind an additional EVM address to the current account (proof via SIWE)."""
    from core.auth_failure_tracker import record_auth_failure

    _require_multichain()

    client_ip = get_remote_address(request)
    _enforce_auth_lockout(client_ip)
    try:
        addr = await recover_siwe_signer_async(
            address=body.address,
            payload=body.nonce_token,
            signature=body.signature,
            domain=resolve_siwe_domain(request.headers.get("host")),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except HTTPException as proof_err:
        # 與 evm-login 同口徑：nonce 過期／格式錯不計暴力嘗試
        if (
            proof_err.status_code == 401
            and "expired" not in str(proof_err.detail).lower()
        ):
            record_auth_failure(client_ip, f"evm bind invalid: {proof_err.status_code}")
        raise

    try:
        binding = await user_wallet_repo.register_binding(
            current_user["user_id"], CHAIN_EVM, addr, is_primary=False
        )
    except ValueError:
        # 衝突：查綁定歸屬區分「重綁同地址（冪等成功）」與「已屬他人（409）」
        existing = await user_wallet_repo.get_binding(CHAIN_EVM, addr)
        if existing and existing["user_id"] == current_user["user_id"]:
            audit_log(
                action="wallet_bound",
                user_id=current_user["user_id"],
                username=current_user.get("username"),
                metadata={
                    "wallet_address": addr,
                    "chain": CHAIN_EVM,
                    "idempotent": True,
                    "ip": client_ip,
                },
            )
            _clear_auth_failures(client_ip)
            return {"success": True, "wallet": existing}
        record_auth_failure(client_ip, "evm bind conflict")
        raise HTTPException(status_code=409, detail="Address already bound")

    audit_log(
        action="wallet_bound",
        user_id=current_user["user_id"],
        username=current_user.get("username"),
        metadata={
            "wallet_address": addr,
            "chain": CHAIN_EVM,
            "ip": client_ip,
        },
    )
    _clear_auth_failures(client_ip)
    return {"success": True, "wallet": binding}


class TelegramLoginRequest(BaseModel):
    init_data: str  # window.Telegram.WebApp.initData (signed by Telegram)


@router.post("/api/user/telegram-login")
@limiter.limit("20/minute")
async def telegram_login(
    request: Request, response: Response, body: TelegramLoginRequest
):
    """
    Authenticate a Telegram Mini App user via signed ``initData``.

    Telegram already vouches for the user's identity, so no wallet signature
    is needed to log in. Resolution:
      - If this telegram_id is already bound (via /link or a prior login),
        log in as that existing account (which may have a wallet).
      - Otherwise auto-create a Telegram-native account (identity=tg_<id>)
        and bind it. The user can connect a wallet later for TON payments.
    """
    from api.telegram_verification import verify_telegram_init_data

    # GAP-1: Telegram initData 驗證失敗也記錄(可能是偽造簽章)。
    from core.auth_failure_tracker import record_auth_failure
    from core.database.telegram import (
        create_telegram_binding,
        get_binding_by_telegram_id,
    )
    from core.database.user import set_user_language
    from core.i18n import language_from_client_code

    client_ip = get_remote_address(request)
    _enforce_auth_lockout(client_ip)

    try:
        tg_user = verify_telegram_init_data(body.init_data)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as verify_err:
        record_auth_failure(
            client_ip, f"telegram init_data invalid: {type(verify_err).__name__}"
        )
        raise
    tg_id = int(tg_user["id"])
    tg_username = tg_user.get("username") or None
    tg_first = tg_user.get("first_name") or None

    try:
        binding = await run_sync(lambda: get_binding_by_telegram_id(tg_id))
        if binding and binding.get("user_id"):
            # Existing linked account (may be wallet-based or a prior tg_ login).
            identity = binding["user_id"]
            result = await run_sync(lambda: create_or_get_user(identity=identity))
        else:
            # First time: create a Telegram-native account and bind it.
            identity = f"tg_{tg_id}"
            username = tg_username or (f"TG_{tg_first}" if tg_first else f"TG_{tg_id}")
            result = await run_sync(
                lambda: create_or_get_user(
                    identity=identity, username=username, auth_method="telegram"
                )
            )
            await run_sync(
                lambda: create_telegram_binding(
                    tg_id, result["user_id"], tg_username, tg_first
                )
            )
            # 第一次登入就把語言偏好種下（Telegram 客戶端語言）：Telegram 回覆與
            # 每日早報讀 users.language，沒種的話英文客戶端會拿到中文（2026-09-13）
            seed_lang = language_from_client_code(tg_user.get("language_code"))
            await run_sync(set_user_language, result["user_id"], seed_lang)

        access_token = create_access_token(
            data={"sub": result["user_id"], "username": result["username"]}
        )
        refresh_token = create_refresh_token(
            data={"sub": result["user_id"], "username": result["username"]}
        )
        set_token_cookies(response, access_token, refresh_token)
        record_guest_converted(  # 轉換漏斗（PR-5）
            request, result["user_id"], "telegram", result.get("is_new", False)
        )

        has_wallet = not str(result["user_id"]).startswith("tg_")
        _clear_auth_failures(client_ip)
        return {
            "success": True,
            "user": {
                "user_id": result["user_id"],
                "username": result["username"],
                "auth_method": result.get("auth_method", "telegram"),
                "role": result.get("role", "user"),
                "membership_tier": result.get("membership_tier", "free"),
                "has_wallet": has_wallet,
            },
            "is_new_user": result.get("is_new", False),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Telegram 登入失敗: %s", e)
        raise HTTPException(status_code=500, detail="Telegram login failed")


# GET /api/user/me 回應快取（30s per-user TTL）。
# /me 被前端高頻呼叫（page-load + i18n 三事件 fan-out，~33/min），每次 2 個 run_sync
# 搶 shared DB executor；Zeabur postgres 偶發慢時 executor 被佔死 → timeout 500
# （2026-08-12 事件）。回應對同一 user 在 TTL 內穩定；下方 setter（語言/暱稱/LLM
# provider）主動 invalidate，改動後即時生效（不必等 TTL）。
_ME_CACHE: TTLCache = TTLCache(maxsize=2048, ttl=30)


async def _has_login_backup(user_id: str) -> bool:
    """綁了 EVM 錢包或 Google（TON 登入拔除後還進得來的方式）。查詢失敗當成有，不亂提醒。"""
    try:
        if await user_wallet_repo.bound_addresses(user_id, CHAIN_EVM):
            return True
        from core.database.google import get_binding_by_user_id

        return bool(await run_sync(get_binding_by_user_id, user_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("login backup check failed for %s: %s", user_id, exc)
        return True


@router.get("/api/user/me")
async def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    """獲取當前登入用戶的資料（30s per-user TTL 快取，setter 主動 invalidate）。"""
    user_id = current_user.get("user_id")
    # 快取命中：直接回，避免 DB round-trip 與 thread pool
    cached = _ME_CACHE.get(user_id) if user_id else None
    if cached is not None:
        return cached
    language = await user_repo.get_language(user_id) if user_id else None
    display_name = await user_repo.get_display_name(user_id) if user_id else None
    # 使用者選的 LLM provider（跨裝置帶回）。None = 從未設定過，前端走既有 fallback。
    selected_provider = (
        await user_llm_preferences_repo.get_selected_provider(user_id)
        if user_id
        else None
    )

    # TON 用戶的 user_id 即錢包地址（已通過 ton_proof 驗證）。
    # auth_method 來自 DB；TEST_MODE fallback 不帶此欄位時，用 user_id 前綴
    # （UQ/EQ/0:）輔助判定，避免把 tg_<id> 誤認為錢包地址。
    # 2026-09-04：原本只認 TON——EVM 使用者的 user_id 是 evm_0x…，對不上任何
    # 一條，wallet_address 一律 None。而登入 2026-08-31 起統一 EVM，等於**每個
    # 登入的人**在 Settings 都看不到自己的錢包地址。
    auth_method = current_user.get("auth_method")
    if auth_method == "ton_wallet":
        wallet_address = user_id
    elif auth_method == "evm_wallet" or (
        isinstance(user_id, str) and user_id.startswith("evm_")
    ):
        # identity 是 evm_<小寫地址>（見 evm_login）——去前綴還原成 0x 地址
        wallet_address = user_id[4:] if str(user_id).startswith("evm_") else None
    elif isinstance(user_id, str) and is_ton_address(user_id):
        wallet_address = user_id
    else:
        wallet_address = None

    # 舊 TON 身份（TON 登入已拔除）只剩 refresh token 撐著，過期就再也進不來——
    # 還沒綁 EVM 錢包或 Google 的，前端要提醒他們綁一個備用登入方式。
    login_backup_needed = False
    if isinstance(user_id, str) and is_ton_address(user_id):
        login_backup_needed = not await _has_login_backup(user_id)

    # 條款／隱私政策改版後的同意狀態（core/legal.py）：accepted=false → 前端顯示同意提示卡。
    # 查不到就回 None（前端不顯示），不擋 /me。
    legal_status = None
    if user_id:
        try:
            from core import legal

            legal_status = {
                "version": legal.LEGAL_VERSION,
                "accepted": await run_sync(legal.has_accepted, user_id),
            }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("legal consent lookup skipped for %s: %s", user_id, exc)

    # 會員到期前站內提醒（≤7 天、3 天內不重複）——crypto 沒有自動續扣，
    # 提醒是留住續費的唯一手段（2026-09-11 盤查）。失敗只 log，不影響 /me。
    try:
        from core.membership_reminder import maybe_notify_membership_expiring

        await maybe_notify_membership_expiring(
            user_id,
            current_user.get("membership_tier"),
            current_user.get("membership_expires_at"),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("membership expiry reminder skipped for %s: %s", user_id, exc)

    response = {
        "success": True,
        "user": {
            "user_id": user_id,
            "username": current_user.get("username"),
            "role": current_user.get("role", "user"),
            "auth_method": auth_method,
            "membership_tier": current_user.get("membership_tier", "free"),
            # 到期日（isoformat）——前端 currentUser 快取首繪即顯示
            # 「Premium Member · {{date}} 到期」用（2026-09-10 review）
            "membership_expires_at": current_user.get("membership_expires_at"),
            # 身份本身是錢包或有綁定列（repositories._user_to_dict）；TEST_MODE 的
            # 合成使用者沒有這欄 → 看身份地址，不再預設 True
            "has_wallet": bool(current_user.get("has_wallet")) or bool(wallet_address),
            "language": language,
            # c010: 個人化暱稱（NULL = 未設定，前端 fallback 到 username）
            "display_name": display_name,
            # 已驗證的錢包地址（Trustworthy AI — Principal: agent 知道在跟誰對話）
            "wallet_address": wallet_address,
            # 使用者上次選的 LLM provider（跨裝置還原）。None = 從未設定。
            "selected_provider": selected_provider,
            # 舊 TON 身份且沒有 EVM／Google 備用登入 → 前端顯示綁定提醒
            "login_backup_needed": login_backup_needed,
            # 條款同意狀態 {version, accepted}；None = 查不到（前端不顯示提示）
            "legal": legal_status,
        },
    }
    if user_id:
        _ME_CACHE[user_id] = response
    return response


# ============================================================================
# 使用者選的 LLM provider（跨裝置帶回）
#
# 背景：selectedProvider 原本只存瀏覽器 localStorage，換裝置/清快取即失，
# 系統改按 provider 清單順序挑（openrouter 在 deepseek 前），導致使用者明明
# 選了 deepseek 卻被帶回 openrouter（額度用完時連帶讓聊天/釐清等功能失效）。
# 詳見 docs/plans/2026-08-06-persist-user-selected-provider-design.md。
# ============================================================================


class UserLLMProviderInput(BaseModel):
    provider: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="使用者選的 LLM provider（須在支援清單內）",
    )


@router.put("/api/user/preferences/llm-provider")
@limiter.limit("30/minute")
async def set_user_llm_provider_pref(
    request: Request,
    body: UserLLMProviderInput,
    current_user: dict = Depends(get_current_user),
):
    """儲存使用者選的 LLM provider（跨裝置帶回）。

    不檢查該 provider 是否已綁金鑰——前端切換時可能先選再綁 key；
    使用時 ``getCurrentProvider`` 仍會在 selected provider 無 key 時 fallback。
    """
    provider = (body.provider or "").strip().lower()
    if provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider. Must be one of: {', '.join(LLM_PROVIDERS)}",
        )
    await user_llm_preferences_repo.set_selected_provider(
        current_user["user_id"], provider
    )
    _ME_CACHE.pop(current_user["user_id"], None)
    return {"success": True, "provider": provider}


# 支援的 UI 語言（與前端 LanguageSwitcher / i18n 對齊）
_SUPPORTED_LANGUAGES = {"zh-TW", "zh-CN", "en", "ru"}


class UserLanguageInput(BaseModel):
    language: str


@router.put("/api/user/language")
@limiter.limit("30/minute")
async def set_user_language_pref(
    request: Request,
    body: UserLanguageInput,
    current_user: dict = Depends(get_current_user),
):
    """儲存使用者的 UI 語言偏好（跨裝置 + 給 Telegram 共用）。"""
    from core.database.user import set_user_language

    lang = (body.language or "").strip()
    if lang not in _SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language. Must be one of: {', '.join(sorted(_SUPPORTED_LANGUAGES))}",
        )

    ok = await run_sync(set_user_language, current_user["user_id"], lang)
    if not ok:
        raise HTTPException(status_code=500, detail="save failed")
    _ME_CACHE.pop(current_user["user_id"], None)
    return {"success": True, "language": lang}


# ============================================================================
# 個人化暱稱（display_name）— Trustworthy AI Hackathon
#
# 讓使用者把預設代稱（TON_<hex>）改成自己想要的暱稱，chat 招呼與 Agent
# system prompt 才能個人化（Principal: agent 知道在跟誰對話）。
#
# 免費改名一次 / 24h；Phase 2 會加「付 TON 立即改名」繞過冷卻。
# ============================================================================


class UserDisplayNameInput(BaseModel):
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="個人化暱稱（1–20 字，不可含換行/控制字元）",
    )


@router.put("/api/user/display-name")
@limiter.limit("5/minute")
async def set_user_display_name_pref(
    request: Request,
    body: UserDisplayNameInput,
    current_user: dict = Depends(get_current_user),
):
    """設定個人化暱稱，含 24h 免費改名冷卻。

    - 成功 → 200 + 新暱稱
    - 24h 內重複 → 429 + ``reason="cooldown"`` + ``next_available_at``
    - 內容不合法（空、過長、含換行/控制字元）→ 422
    - 暱稱已被他人使用 → 409 + ``reason="duplicate"``
    - 暱稱使用系統預設名格式（``TON_``／``EVM_``／``TG_``… 開頭）→ 422 + ``reason="reserved"``
    """
    import re

    name = (body.display_name or "").strip()
    if not name or len(name) > 20:
        raise HTTPException(status_code=422, detail="display_name invalid")
    # 擋控制字元 / 換行（\r \n \t 與其他 C0/C1 control codes）
    if re.search(r"[\x00-\x1f\x7f-\x9f]", name):
        raise HTTPException(status_code=422, detail="display_name invalid")

    # 禁止系統預設名格式:TON_／EVM_／TG_／G_… 開頭是自動產生的識別(見
    # SYSTEM_USERNAME_PREFIXES),使用者取相同就能冒充別人的預設名。不分大小寫。
    # 也不可等於任何人的 username。
    if name.upper().startswith(tuple(p.upper() for p in SYSTEM_USERNAME_PREFIXES)):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "reserved",
                "message": "Cannot use a system default name format (e.g. starting with "
                + ", ".join(SYSTEM_USERNAME_PREFIXES)
                + ")",
            },
        )

    user_id = current_user["user_id"]

    # 預檢查:暱稱是否已被他人使用(DB 唯一索引做最終把關,此處提前擋以給清楚錯誤)
    if await run_sync(is_display_name_taken, name, user_id):
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "duplicate",
                "message": "This display name is already taken",
            },
        )
    # 不可等於任何人的 username(系統預設名)
    if await run_sync(is_username_taken, name):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "reserved",
                "message": "This name is reserved by the system and cannot be used",
            },
        )

    result = await run_sync(set_user_display_name, user_id, name)
    ok = result[0]
    reason = result[1] if len(result) > 1 else None

    if ok:
        # /me 回應快取的 display_name 已變，主動 invalidate（見 _ME_CACHE）
        _ME_CACHE.pop(user_id, None)
        # 清除舊 greeting 快取,避免改名後歡迎訊息仍顯示舊暱稱長達 1 小時
        # lazy import 避免 router 模組間頂層耦合
        from api.routers.analysis import invalidate_greeting_cache

        invalidate_greeting_cache(user_id)

        # audit：rename 屬敏感操作（身份變更），記錄可追溯（audit_log 為 sync）
        audit_log(
            "rename_display_name",
            user_id=user_id,
            username=current_user.get("username"),
            resource_type="user",
            resource_id=user_id,
            request_data={"new_name": name},
            success=True,
        )
        return {"success": True, "display_name": name}

    if reason == "duplicate":
        # 競態:預檢查與 UPDATE 之間有人搶先取走同名
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "duplicate",
                "message": "This display name is already taken",
            },
        )

    if reason == "cooldown":
        next_available = result[2] if len(result) > 2 else None
        # audit 失敗嘗試（可疑：可能是繞過冷卻的濫用）
        audit_log(
            "rename_display_name",
            user_id=user_id,
            username=current_user.get("username"),
            resource_type="user",
            resource_id=user_id,
            request_data={"new_name": name},
            success=False,
            error_message="cooldown",
        )
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "cooldown",
                "message": "You can change it for free only once every 24 hours. Please try again later.",
                "next_available_at": next_available,
            },
        )

    # db_error 或其他
    raise HTTPException(status_code=500, detail="save failed")


class RefreshTokenRequest(BaseModel):
    refresh_token: str


@router.post("/api/user/refresh")
# 30/min：access token 過期時 limiter key 退化為 IP（共享 NAT 會互撞）；
# 前端 visibility/pageShow/30-min 多路徑皆可能觸發主動刷新，10/min 實測會 429
# （2026-08-14 平台測試觀察）。JWT re-issue 本身輕量，30/min 仍具暴力防護。
@limiter.limit("30/minute")
async def refresh_access_token(
    request: Request, response: Response, body: RefreshTokenRequest = None
):
    """
    使用 refresh token 獲取新的 access token。
    Reads refresh_token from cookie first, falls back to request body.
    """
    from api.deps import REFRESH_TOKEN_COOKIE, verify_token

    # GAP-1: auth 失敗自動偵測。失敗時記錄,達閾值發 BRUTE_FORCE_ATTEMPT event。
    from core.auth_failure_tracker import record_auth_failure

    client_ip = get_remote_address(request)
    _enforce_auth_lockout(client_ip)

    refresh_token_value = body.refresh_token if body and body.refresh_token else None
    if not refresh_token_value:
        refresh_token_value = request.cookies.get(REFRESH_TOKEN_COOKIE)

    if not refresh_token_value:
        record_auth_failure(client_ip, "refresh token missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is required",
        )

    # Check if the refresh token has been revoked (e.g., from logout)
    from api.deps import is_token_revoked

    if is_token_revoked(refresh_token_value):
        record_auth_failure(client_ip, "refresh token revoked")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    try:
        payload = verify_token(refresh_token_value, allow_refresh=True)
        if payload.get("type") != "refresh":
            record_auth_failure(client_ip, "invalid refresh token type")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        user_id = payload.get("sub")
        username = payload.get("username")

        if not user_id:
            record_auth_failure(client_ip, "invalid token payload")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        # 停權／刪除的用戶不得刷新——否則回 success 之後每個 API 都 403，
        # 前端 session 狀態與後端矛盾（2026-09-10 登入鏈徹查）。
        user = await user_repo.get_by_id(user_id)
        if not user or not user.get("is_active", True):
            record_auth_failure(client_ip, "refresh for inactive/missing user")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account has been suspended",
            )

        new_access_token = create_access_token(
            data={"sub": user_id, "username": username}
        )
        new_refresh_token = create_refresh_token(
            data={"sub": user_id, "username": username}
        )

        set_token_cookies(response, new_access_token, new_refresh_token)

        # 輪換撤銷：舊 refresh token 作廢——被竊的舊顆不再可用到 30 天
        # （此前只換新不廢舊，logout 也只撤銷 logout 當下那顆）。
        from api.deps import revoke_token as _revoke_token

        _revoke_token(refresh_token_value)

        _clear_auth_failures(client_ip)
        return {
            "success": True,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        record_auth_failure(client_ip, f"refresh exception: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token refresh failed",
        )


@router.post("/api/user/logout")
@limiter.limit("30/minute")
async def logout(request: Request, response: Response):
    """Clear JWT cookies on logout and revoke the refresh and access tokens."""
    from api.deps import (
        ACCESS_TOKEN_COOKIE,
        REFRESH_TOKEN_COOKIE,
        revoke_access_token,
        revoke_token,
    )

    # Revoke the refresh token to prevent token reuse after logout
    refresh_token_value = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token_value:
        revoke_token(refresh_token_value)

    # access token 也要當場失效,不然被複製走的那顆能用到 exp。cookie 與 Bearer 都撤
    # (前端記憶體可能還留著一顆舊 Bearer);過期／偽造的在 revoke_access_token 裡略過。
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    for access_token_value in {request.cookies.get(ACCESS_TOKEN_COOKIE), bearer}:
        if not access_token_value:
            continue
        try:
            revoke_access_token(access_token_value)
        except Exception:
            # 登出不能因為撤銷失敗而失敗(cookie 照清)
            logger.warning("logout: access token revoke failed", exc_info=True)

    clear_token_cookies(response)
    return {"success": True}


# --- Pi Payment Handling Endpoints ---


class ClientLogRequest(BaseModel):
    source: str = Field(..., description="Log source (e.g., 'premium', 'forum')")
    level: str = Field(default="info", description="Log level")
    message: str = Field(..., description="Log message")
    data: Optional[dict] = Field(default=None, description="Additional data")


_VALID_CLIENT_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})


@router.post("/api/client/log")
@limiter.limit("30/minute")
async def client_log(
    request: Request,
    body: ClientLogRequest,
    current_user: dict = Depends(get_current_user),
):
    """接收前端 client-side logs 並寫入 server logs"""
    user_id = current_user.get("user_id", "unknown")
    safe_level = (
        body.level.lower() if body.level.lower() in _VALID_CLIENT_LOG_LEVELS else "info"
    )
    safe_source = body.source.replace("\n", " ").replace("\r", " ")
    safe_message = body.message.replace("\n", " ").replace("\r", " ")
    log_msg = f"[CLIENT:{safe_source}] [{safe_level.upper()}] {safe_message}"

    if body.data:
        safe_data = str(body.data).replace("\n", " ").replace("\r", " ")
        log_msg += f" | data: {safe_data}"

    log_msg += f" | user: {user_id}"

    log_func = getattr(logger, safe_level)
    log_func(log_msg)

    return {"status": "ok"}


@router.get("/api/user/wallet-status")
async def get_wallet_status(current_user: dict = Depends(get_current_user)):
    """獲取用戶錢包綁定狀態"""
    try:
        user_id = current_user["user_id"]
        if TEST_MODE and (
            user_id == TEST_USER.get("uid") or user_id.startswith("test-user-")
        ):
            return {
                "success": True,
                "has_wallet": True,
                "auth_method": "dev_test",
                "wallet_address": None,
            }

        status = await run_sync(get_user_wallet_status, user_id)
        return {"success": True, **status}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get wallet status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch status")


# --- User API Key Management Endpoints ---


class SaveAPIKeyRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


class SaveModelRequest(BaseModel):
    provider: str
    model: str


def _reject_keyless(provider: str) -> None:
    """平台模型（keyless）沒有金鑰可綁／刪／測——它是每個人預設就有的綁定。"""
    from core.model_config import is_keyless_provider

    if is_keyless_provider((provider or "").strip().lower()):
        raise HTTPException(
            status_code=400,
            detail="This provider is provided by the platform and needs no API key.",
        )


@router.post("/api/user/api-keys")
@limiter.limit("10/minute")
async def save_user_api_key_endpoint(
    request: Request,
    req: SaveAPIKeyRequest,
    current_user: dict = Depends(get_current_user),
):
    """儲存用戶的 API Key（加密後存入資料庫）"""
    from core.orm.user_api_keys_repo import user_api_keys_repo

    _reject_keyless(req.provider)
    user_id = current_user["user_id"]
    try:
        result = await user_api_keys_repo.save_user_api_key(
            user_id, req.provider, req.api_key, req.model
        )
        if not result["success"]:
            raise HTTPException(
                status_code=400, detail=result.get("error", "Failed to save API key")
            )

        logger.info(f"API key saved for user {user_id}, provider: {req.provider}")
        audit_log(
            action="api_key_saved",
            user_id=user_id,
            metadata={"provider": req.provider},
        )
        # BYOK 金鑰變動會改變 /api/user/tools 回應中的 key_status，需失效快取
        try:
            from api.routers.tools import invalidate_tools_cache

            invalidate_tools_cache(user_id=user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[api-keys] Failed to invalidate tools cache: {e}")
        # 金鑰變動後，舊 session 的 ManagerAgent 仍持有用舊 key compile 的 graph，
        # 必須清掉該 user 所有 cached agents，下次對話才會用新 key 重建。
        try:
            from core.agents.bootstrap import invalidate_manager_cache

            invalidate_manager_cache(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[api-keys] Failed to invalidate manager cache: {e}")
        return {"success": True, "message": "API key saved securely"}

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Save API key error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save")


@router.get("/api/user/api-keys")
async def get_user_api_keys_endpoint(
    kind: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """獲取用戶所有 API Key 的狀態（遮蔽版本，用於前端顯示）。

    可選 ``kind`` 查詢參數（"llm" | "tool"）僅回傳該類別的供應商。
    """
    from core.orm.user_api_keys_repo import user_api_keys_repo

    if kind is not None and kind not in ("llm", "tool"):
        raise HTTPException(status_code=400, detail="Invalid kind")

    user_id = current_user["user_id"]
    try:
        keys = await user_api_keys_repo.get_all_user_api_keys(user_id, kind=kind)
        return {"success": True, "keys": keys}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get API keys error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch")


@router.get("/api/user/tool-providers")
async def get_tool_providers_endpoint(current_user: dict = Depends(get_current_user)):
    """回傳所有 BYOK 工具供應商的前端顯示資訊（名稱、用途、申請連結）。"""
    from core.orm.user_api_keys_repo import tool_provider_meta

    return {"success": True, "providers": tool_provider_meta()}


@router.get("/api/user/api-keys/{provider}")
async def get_user_api_key_endpoint(
    provider: str, current_user: dict = Depends(get_current_user)
):
    """獲取特定 provider 的 API Key 狀態（遮蔽版本）"""
    from core.orm.user_api_keys_repo import user_api_keys_repo

    user_id = current_user["user_id"]
    try:
        key_info = await user_api_keys_repo.get_user_api_key_masked(user_id, provider)
        return {"success": True, **key_info}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get API key error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch")


@router.delete("/api/user/api-keys/{provider}")
@limiter.limit("10/minute")
async def delete_user_api_key_endpoint(
    request: Request,
    provider: str,
    model: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """刪除用戶的 API Key。

    帶 ``model`` 查詢參數時只刪該筆保存模型（刪到最後一個模型才連金鑰一併解綁）；
    不帶則整個 provider 解綁（金鑰＋全部保存模型）。
    """
    _reject_keyless(provider)
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", provider):
        raise HTTPException(status_code=400, detail="Invalid provider name")
    from core.orm.user_api_keys_repo import user_api_keys_repo

    user_id = current_user["user_id"]
    model_name = model.strip() if model else None
    try:
        if model_name:
            result = await user_api_keys_repo.delete_saved_model(
                user_id, provider, model_name
            )
        else:
            result = await user_api_keys_repo.delete_user_api_key(user_id, provider)
        if not result["success"]:
            raise HTTPException(
                status_code=400, detail=result.get("error", "Failed to delete")
            )

        logger.info(f"API key deleted for user {user_id}, provider: {provider}")
        metadata = {"provider": provider}
        if model_name:
            metadata["model"] = model_name
        audit_log(action="api_key_deleted", user_id=user_id, metadata=metadata)
        # BYOK 金鑰變動會改變 /api/user/tools 回應中的 key_status，需失效快取
        try:
            from api.routers.tools import invalidate_tools_cache

            invalidate_tools_cache(user_id=user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[api-keys] Failed to invalidate tools cache: {e}")
        # 金鑰刪除後，舊 session 的 ManagerAgent 仍持有用舊 key compile 的 graph，
        # 必須清掉該 user 所有 cached agents，下次對話才會正確 fallback。
        try:
            from core.agents.bootstrap import invalidate_manager_cache

            invalidate_manager_cache(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[api-keys] Failed to invalidate manager cache: {e}")
        # 模型級刪除時把 provider_removed / active_model 回給前端，
        # 讓 UI 知道 provider 是否仍綁定、使用中的模型換成了誰
        extras = {k: v for k, v in result.items() if k != "success"}
        return {"success": True, "message": "API key deleted", **extras}

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Delete API key error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete")


class TestAPIKeyRequest(BaseModel):
    api_key: Optional[str] = None


@router.post("/api/user/api-keys/{provider}/test")
@limiter.limit("5/minute")
async def test_user_api_key_endpoint(
    request: Request,
    provider: str,
    req: Optional[TestAPIKeyRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """測試 BYOK 金鑰是否有效。body 不給 api_key 時讀使用者已儲存的金鑰。"""
    import re

    _reject_keyless(provider)

    if not re.match(r"^[a-zA-Z0-9_-]+$", provider):
        raise HTTPException(status_code=400, detail="Invalid provider name")

    from core.orm.user_api_keys_repo import user_api_keys_repo
    from core.tools.key_tester import test_provider_key

    user_id = current_user["user_id"]
    api_key = req.api_key if req and req.api_key else None
    if not api_key:
        stored = await user_api_keys_repo.get_user_api_key(user_id, provider)
        if not stored:
            raise HTTPException(
                status_code=400,
                detail=f"No {provider} API key has been set. Unable to test.",
            )
        api_key = stored

    # 測試呼叫是 sync requests，用 run_sync 包起來避免 block event loop
    from api.utils import run_sync

    result = await run_sync(test_provider_key, provider, api_key)
    audit_log(
        action="api_key_tested",
        user_id=user_id,
        metadata={
            "provider": provider,
            "success": result.get("success"),
            "status_code": result.get("status_code"),
        },
    )
    return result


_FEEDBACK_MSGS = {
    "success": {
        "zh-TW": "回饋已送出",
        "zh-CN": "反馈已提交",
        "en": "Feedback submitted successfully",
        "ru": "Отзыв успешно отправлен",
    },
    "empty": {
        "zh-TW": "回饋內容不可為空",
        "zh-CN": "反馈内容不能为空",
        "en": "Feedback message cannot be empty",
        "ru": "Сообщение не может быть пустым",
    },
    "failed": {
        "zh-TW": "送出失敗，請稍後再試",
        "zh-CN": "提交失败，请稍后再试",
        "en": "Failed to submit feedback",
        "ru": "Не удалось отправить отзыв",
    },
}


def _feedback_msg(key: str, language: str) -> str:
    lang = language if language in ("zh-TW", "zh-CN", "en", "ru") else "zh-TW"
    return _FEEDBACK_MSGS.get(key, {}).get(lang, key)


@router.post("/api/user-feedback")
@limiter.limit("10/minute")
async def submit_user_feedback(
    request: Request,
    body: UserFeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """Receive platform feedback submitted from the settings page."""
    language = body.language
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail=_feedback_msg("empty", language))

    user_id = current_user["user_id"]
    username = current_user.get("username", "")

    try:
        existing_user = await run_sync(get_user_by_id, user_id)
        if not existing_user:
            # 沒帶 auth_method → create_or_get_user 依身份格式推（evm_／tg_／g_），
            # 認不出來存 NULL——不再把不知道是誰的帳號建成 ton_wallet
            fallback_auth_method = current_user.get("auth_method")
            if not fallback_auth_method:
                fallback_auth_method = (
                    "dev_test"
                    if TEST_MODE and str(user_id).startswith("test-user-")
                    else None
                )
            await run_sync(
                create_or_get_user,
                user_id,
                username or None,
                fallback_auth_method,
            )
        await run_sync(save_user_feedback, user_id, username, message)
        audit_log(
            action="submit_user_feedback",
            user_id=user_id,
            metadata={"length": len(message)},
        )
        return {"success": True, "message": _feedback_msg("success", language)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Submit user feedback error: {e}")
        raise HTTPException(status_code=500, detail=_feedback_msg("failed", language))


@router.post("/api/user/api-keys/model")
@limiter.limit("10/minute")
async def save_user_model_endpoint(
    request: Request,
    req: SaveModelRequest,
    current_user: dict = Depends(get_current_user),
):
    """儲存用戶選擇的模型（不更改 API Key）"""
    from core.orm.user_api_keys_repo import user_api_keys_repo

    user_id = current_user["user_id"]
    try:
        result = await user_api_keys_repo.save_user_model_selection(
            user_id, req.provider, req.model
        )
        if not result["success"]:
            error_msg = result.get("error", "Failed to save model")
            if "No API key" in error_msg:
                return {"success": False, "message": error_msg}
            raise HTTPException(status_code=400, detail=error_msg)
        # 模型變動等同 LLM client 換新，舊 session 的 graph 也是用舊模型 compile，
        # 必須清掉 cached agents 確保下次對話用新模型。
        try:
            from core.agents.bootstrap import invalidate_manager_cache

            invalidate_manager_cache(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[api-keys] Failed to invalidate manager cache: {e}")
        return {"success": True, "message": "Model selection saved"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Save model error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save")


@router.get("/api/user/llm-debug")
async def llm_debug(
    request: Request,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """診斷 AI 深度分析所需的認證與 API Key 狀態。"""
    from api.user_llm import resolve_user_llm_credentials

    has_cookie = bool(request.cookies.get("access_token"))
    is_authed = bool(current_user and current_user.get("user_id"))
    user_id = current_user.get("user_id") if is_authed else None

    creds = (
        await resolve_user_llm_credentials(current_user, None) if is_authed else None
    )

    return {
        "has_cookie": has_cookie,
        "is_authenticated": is_authed,
        "user_id": user_id,
        "has_api_key": bool(creds),
        "provider": creds["provider"] if creds else None,
        "diagnosis": (
            "✅ Authentication and API key are OK; deep analysis should work"
            if creds
            else "❌ Not logged in. Please refresh the page or log in again"
            if not is_authed
            else "❌ Logged in but no API key found. Please re-save your key in Settings"
        ),
    }


# 2026-09-09 設計（per-agent prompt 遷移 user_agent_configs）：runtime
# 只讀 'chat'，crypto/tw_stock/us_stock 三個鍵自 PLAN 時代起就是死資料——
# 不再收新寫入（既有列不動，GET 仍可讀回）。per-agent 指示改存
# user_agent_configs.system_prompt（AI Studio agent 卡）。
_VALID_AGENT_IDS = frozenset({"chat"})


@router.get("/api/user/analysis-preferences")
async def get_analysis_preferences(
    current_user: dict = Depends(get_current_user),
):
    from core.database.preferences import get_all_preferences

    user_id = current_user["user_id"]
    try:
        prefs = await run_sync(get_all_preferences, user_id)
        return {
            "success": True,
            "preferences": [
                {
                    "agent_id": p.get("agent_id"),
                    "system_prompt": p.get("system_prompt"),
                    "enabled_tools": p.get("enabled_tools"),
                    "updated_at": p.get("updated_at"),
                }
                for p in prefs
            ],
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get analysis preferences error: {e}")
        raise HTTPException(status_code=500, detail="get failed")


@router.put("/api/user/analysis-preferences")
@limiter.limit("20/minute")
async def upsert_analysis_preference(
    request: Request,
    body: AnalysisPreferenceInput,
    current_user: dict = Depends(get_current_user),
):
    from core.database.preferences import upsert_preference
    from core.database.user import get_user_membership

    user_id = current_user["user_id"]
    membership = await run_sync(get_user_membership, user_id)
    if not membership.get("is_premium"):
        raise HTTPException(status_code=403, detail="Premium only")

    if body.agent_id not in _VALID_AGENT_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_id. Must be one of: {', '.join(sorted(_VALID_AGENT_IDS))}",
        )

    if body.system_prompt and len(body.system_prompt) > 2000:
        raise HTTPException(status_code=400, detail="Too long")

    # 過濾 system prompt 的越權/jailbreak 模式(防禦深度,即使目前 system_prompt
    # 注入點尚未接線,預先設防)。Pydantic 層已擋 >2000 字,這裡擋惡意內容。
    from core.agents.prompt_guard import sanitize_system_prompt

    if body.system_prompt:
        body.system_prompt = sanitize_system_prompt(body.system_prompt)

    try:
        result = await run_sync(
            upsert_preference,
            user_id,
            body.agent_id,
            body.system_prompt,
            body.enabled_tools,
        )
        return {
            "success": True,
            "preference": {
                "agent_id": result.get("agent_id"),
                "system_prompt": result.get("system_prompt"),
                "enabled_tools": result.get("enabled_tools"),
                "updated_at": result.get("updated_at"),
            },
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Upsert analysis preference error: {e}")
        raise HTTPException(status_code=500, detail="save failed")


@router.delete("/api/user/analysis-preferences/{agent_id}")
@limiter.limit("20/minute")
async def delete_analysis_preference(
    request: Request,
    agent_id: str,
    current_user: dict = Depends(get_current_user),
):
    from core.database.preferences import delete_preference
    from core.database.user import get_user_membership

    user_id = current_user["user_id"]
    membership = await run_sync(get_user_membership, user_id)
    if not membership.get("is_premium"):
        raise HTTPException(status_code=403, detail="Premium only")

    if agent_id not in _VALID_AGENT_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_id. Must be one of: {', '.join(sorted(_VALID_AGENT_IDS))}",
        )

    try:
        deleted = await run_sync(delete_preference, user_id, agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="not found")
        return {"success": True, "message": f"{agent_id} deleted"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Delete analysis preference error: {e}")
        raise HTTPException(status_code=500, detail="delete failed")


# ============================================================================
