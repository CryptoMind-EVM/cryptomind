from unittest.mock import AsyncMock, patch

import pytest

from api.deps import create_access_token, create_refresh_token
from core.legal import LEGAL_VERSION


@pytest.mark.asyncio
async def test_user_me_returns_session_restore_fields(client):
    token = create_access_token(data={"sub": "ton-user-123", "username": "TonUser"})

    mocked_user = {
        "user_id": "ton-user-123",
        "username": "TonUser",
        "role": "user",
        "auth_method": "ton_wallet",
        "membership_tier": "premium",
        # 到期日（2026-09-10 review：/me 帶 membership_expires_at 供前端
        # 快取首繪顯示到期日；此 mock 故意不含 → 回 None 的透傳行為也一併覆蓋）
        "has_wallet": True,
        "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.user.user_repo.get_language", new=AsyncMock(return_value=None)), \
         patch("api.routers.user.user_repo.get_display_name", new=AsyncMock(return_value=None)), \
         patch("api.routers.user.user_llm_preferences_repo.get_selected_provider", new=AsyncMock(return_value=None)):
        response = await client.get(
            "/api/user/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["user"] == {
        "user_id": "ton-user-123",
        "username": "TonUser",
        "role": "user",
        "auth_method": "ton_wallet",
        "membership_tier": "premium",
        "membership_expires_at": None,  # user dict 未含時透傳 None（有值時原樣帶回）
        "has_wallet": True,
        "language": None,
        "display_name": None,
        "wallet_address": "ton-user-123",  # auth_method=ton_wallet → user_id 即錢包地址
        "selected_provider": None,  # #356 後 /user/me 帶 LLM provider 偏好（未設定 → None）
        # 2026-09-25：舊 TON 身份沒備用登入才 True（ton-user-123 不是 TON 地址）
        "login_backup_needed": False,
        # 條款重新同意（#922）：/me 帶目前條款版本與使用者是否已同意（沒有紀錄 → False）
        "legal": {"accepted": False, "version": LEGAL_VERSION},
    }


@pytest.mark.asyncio
async def test_refresh_uses_refresh_cookie_without_exposing_tokens_in_json(client):
    refresh_token = create_refresh_token(
        data={"sub": "ton-user-123", "username": "TonUser"}
    )
    client.cookies.set("refresh_token", refresh_token)

    # 2026-09-10 徹查：refresh 現在會檢查用戶存在與 is_active（停權不得刷新）
    mocked_user = {
        "user_id": "ton-user-123",
        "username": "TonUser",
        "role": "user",
        "is_active": True,
    }
    with patch(
        "api.routers.user.user_repo.get_by_id",
        new=AsyncMock(return_value=mocked_user),
    ):
        response = await client.post("/api/user/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "access_token" not in payload
    assert "refresh_token" not in payload

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(header.startswith("access_token=") for header in set_cookie_headers)
    assert any(header.startswith("refresh_token=") for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_refresh_rejects_when_refresh_cookie_missing(client):
    response = await client.post("/api/user/refresh")

    assert response.status_code == 401
    assert response.json()["detail"] == "Refresh token is required"


@pytest.mark.asyncio
async def test_wallet_status_ton_user_linked(client):
    token = create_access_token(data={"sub": "ton-user-456", "username": "TonWallet"})

    mocked_user = {
        "user_id": "ton-user-456",
        "username": "TonWallet",
        "role": "user",
        "auth_method": "ton_wallet",
        "membership_tier": "free",
        "has_wallet": True,
        "is_active": True,
    }

    mocked_wallet_status = {
        "has_wallet": True,
        "auth_method": "ton_wallet",
    }

    with (
        patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)),
        patch(
            "api.routers.user.run_sync",
            new=AsyncMock(return_value=mocked_wallet_status),
        ),
    ):
        response = await client.get(
            "/api/user/wallet-status",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["has_wallet"] is True
    assert payload["auth_method"] == "ton_wallet"
