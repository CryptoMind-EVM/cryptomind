"""Trust Score feature tests — Phase A0

涵蓋：
    - 單元：scoring orchestrator（mock collectors）、onchain helper（mock httpx）
    - router：GET /score（自己看）、GET /tier/{user_id}（別人看徽章）、POST /recompute
    - 分層揭露：tier 查詢不洩漏 breakdown

對應 design doc docs/plans/2026-08-08-trust-score-retention-design.md。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 真的 TON 地址（onchain_signals 會解碼驗證，不能再用 "EQabc" 這種前綴假地址）
TON_WALLET = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"

# ──────────────────────────────────────────────────────────────────────────────
# 單元：onchain_signals（mock httpx）
# ──────────────────────────────────────────────────────────────────────────────


def _make_tonapi_client(account_resp, tx_resps):
    """組 mock httpx client：依 URL 分別回 account / transactions 回應。

    tx_resps: list of dicts，每個是 transactions endpoint 的一頁回應。
    """
    from core.identity.onchain_signals import _TONAPI_ACCOUNT_URL, _TONAPI_TX_URL

    account_mock = MagicMock()
    account_mock.status_code = 200
    account_mock.json.return_value = account_resp

    tx_mocks = []
    for page in tx_resps:
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = page
        tx_mocks.append(m)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def _get(url, params=None):
        if url.startswith(_TONAPI_ACCOUNT_URL.split("{")[0]):
            return account_mock
        if url.startswith(_TONAPI_TX_URL.split("{")[0]):
            # 依翻頁順序回傳（第一次打 tx = 第一頁）
            idx = mock_client._tx_page_count
            mock_client._tx_page_count += 1
            if idx < len(tx_mocks):
                return tx_mocks[idx]
            # 沒頁了 → 空頁
            empty = MagicMock()
            empty.status_code = 200
            empty.json.return_value = {"transactions": [], "next_from": None}
            return empty
        raise AssertionError(f"Unexpected URL: {url}")

    mock_client._tx_page_count = 0
    mock_client.get = AsyncMock(side_effect=_get)
    return mock_client


@pytest.mark.asyncio
async def test_onchain_signals_uses_first_tx_as_age():
    """老錢包近期活躍 → 用第一筆交易時間（不是 last_activity）。核心 bug 修復驗證。"""
    from core.identity.onchain_signals import fetch_wallet_onchain_signals

    # 老錢包：第一筆交易在 2023（老），最後活動在 2026（近期活躍）
    old_first_tx = 1700000000  # 2023-11
    recent_activity = 1785343198  # 2026-07（近）
    account_resp = {"last_activity": recent_activity, "status": "active"}
    tx_resps = [
        {"transactions": [{"utime": recent_activity}, {"utime": old_first_tx}], "next_from": None},
    ]
    mock_client = _make_tonapi_client(account_resp, tx_resps)

    with patch("core.identity.onchain_signals.httpx.AsyncClient", return_value=mock_client), \
         patch("core.identity.onchain_signals.get_json", return_value=None), \
         patch("core.identity.onchain_signals.set_json"):
        result = await fetch_wallet_onchain_signals(TON_WALLET)

    # 用第一筆交易時間（老），不是 last_activity（近）→ 老錢包拿到老年齡
    assert result["first_active"] == datetime.fromtimestamp(old_first_tx, tz=timezone.utc)
    assert result["first_tx_time"] == old_first_tx
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_onchain_signals_uses_cache_when_present():
    """快取有值 → 不翻頁，直接用快取（錢包年齡不變，快取是對的）。"""
    from core.identity.onchain_signals import fetch_wallet_onchain_signals

    cached_first_tx = 1600000000  # 2020
    account_resp = {"last_activity": 1785343198, "status": "active"}

    # tx endpoint 不該被打（快取命中）
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    account_mock = MagicMock()
    account_mock.status_code = 200
    account_mock.json.return_value = account_resp
    mock_client.get = AsyncMock(return_value=account_mock)

    with patch("core.identity.onchain_signals.httpx.AsyncClient", return_value=mock_client), \
         patch("core.identity.onchain_signals.get_json", return_value=cached_first_tx):
        result = await fetch_wallet_onchain_signals(TON_WALLET)

    assert result["first_active"] == datetime.fromtimestamp(cached_first_tx, tz=timezone.utc)
    # 只打了 account（1 次），沒打 tx 翻頁
    assert mock_client.get.await_count == 1


@pytest.mark.asyncio
async def test_onchain_signals_falls_back_to_last_activity_when_no_tx():
    """無交易紀錄 → 回退 last_activity（保守，不歸零）。"""
    from core.identity.onchain_signals import fetch_wallet_onchain_signals

    last_activity = 1750000000
    account_resp = {"last_activity": last_activity, "status": "active"}
    # transactions 空（錢包沒交易）→ 回退
    tx_resps = [{"transactions": [], "next_from": None}]
    mock_client = _make_tonapi_client(account_resp, tx_resps)

    with patch("core.identity.onchain_signals.httpx.AsyncClient", return_value=mock_client), \
         patch("core.identity.onchain_signals.get_json", return_value=None), \
         patch("core.identity.onchain_signals.set_json"):
        result = await fetch_wallet_onchain_signals(TON_WALLET)

    assert result["first_active"] == datetime.fromtimestamp(last_activity, tz=timezone.utc)
    assert result["first_tx_time"] is None


@pytest.mark.asyncio
async def test_onchain_signals_404_returns_none_signals():
    """404（未初始化錢包）→ graceful，不報錯。"""
    from core.identity.onchain_signals import fetch_wallet_onchain_signals

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("core.identity.onchain_signals.httpx.AsyncClient", return_value=mock_client), \
         patch("core.identity.onchain_signals.get_json", return_value=None):
        result = await fetch_wallet_onchain_signals(TON_WALLET)

    assert result["first_active"] is None
    assert result["tx_count"] is None
    assert result["is_active"] is False


@pytest.mark.asyncio
async def test_onchain_signals_invalid_address_returns_error():
    """非法地址 → error dict，不打網路。"""
    from core.identity.onchain_signals import fetch_wallet_onchain_signals

    result = await fetch_wallet_onchain_signals("not-a-ton-address")
    assert "error" in result


# ──────────────────────────────────────────────────────────────────────────────
# 單元：scoring orchestrator（mock collectors + DB）
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recompute_persists_score_and_returns_breakdown():
    """recompute_user_trust 聚合訊號 → persist → 回 score/tier/breakdown。"""
    from core.identity import scoring

    fixed_now = datetime(2024, 8, 8, tzinfo=timezone.utc)  # 2 年前 → wallet_age 滿分
    onchain_signals = {"first_active": fixed_now, "tx_count": 100, "is_active": True}

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "collect_onchain_signals", new=AsyncMock(return_value=onchain_signals)), \
         patch.object(scoring, "collect_passport_stamps", new=AsyncMock(
             return_value={"stamps": {"gitcoin_passport": True}, "reason": "verified",
                           "passport_score": 80.0, "graduated_bonus": 16.0})), \
         patch.object(scoring, "collect_scam_penalty", return_value={"penalty": 0, "reason": "clean"}), \
         patch.object(scoring, "collect_activity_signals",
                      return_value={"score": 20.0, "inactive_days": 0, "reason": "computed"}), \
         patch.object(scoring, "get_connection", return_value=mock_conn):
        # 真的 TON 地址：鏈上訊號只對真的錢包身份採集（"EQtest123" 這種假地址
        # 以前會被當成 TON 錢包去打 TonAPI）
        result = await scoring.recompute_user_trust(
            TON_WALLET, wallet_verified=True, reason="test"
        )

    assert result is not None
    assert result["user_id"] == TON_WALLET
    # wallet_ownership(30) + wallet_age(20) + wallet_activity(15)
    # + activity_bonus(20) + passport_bonus(16, 漸進: 80/100×20) = 101 → cap 100
    assert result["trust_score"] == 100
    assert result["tier"] == "strong_verified"
    assert "base_assessment" in result["breakdown"]
    assert "scam_penalty" in result["breakdown"]
    assert result["breakdown"]["passport_bonus_applied"] == 16.0
    assert "activity" in result["breakdown"]
    assert result["breakdown"]["activity_bonus_applied"] == 20.0
    # persist 被呼叫（INSERT + UPDATE）
    assert mock_cursor.execute.call_count >= 2
    mock_conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_recompute_with_scam_penalty_floors_at_zero():
    """詐騙 DB 命中 → penalty 100 → 分數被壓到 0（floor）。"""
    from core.identity import scoring

    with patch.object(scoring, "collect_onchain_signals", new=AsyncMock(
            return_value={"first_active": None, "tx_count": None, "is_active": False})), \
         patch.object(scoring, "collect_passport_stamps", new=AsyncMock(
             return_value={"stamps": {}, "reason": "passport_not_configured"})), \
         patch.object(scoring, "collect_scam_penalty",
                      return_value={"penalty": 100, "reason": "scam_report_hit"}), \
         patch.object(scoring, "collect_activity_signals",
                      return_value={"score": 0, "inactive_days": 200, "reason": "computed"}), \
         patch.object(scoring, "get_connection", return_value=MagicMock()):
        result = await scoring.recompute_user_trust(TON_WALLET, reason="event_scam_hit")

    assert result is not None
    assert result["trust_score"] == 0  # baseline 30 - penalty 100 + activity 0 → floor 0
    assert result["tier"] == "anonymous"
    assert result["breakdown"]["penalty_applied"] == 100


# ──────────────────────────────────────────────────────────────────────────────
# 單元：activity_score（指數衰減，半衰期 30 天）
# ──────────────────────────────────────────────────────────────────────────────


def test_activity_score_today_active_is_max():
    """今天活躍（0 天）→ 滿分 _MAX_ACTIVITY。"""
    from core.identity import scoring

    now = datetime.now(timezone.utc)
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (now,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_activity_signals("EQactive")

    assert result["score"] == pytest.approx(20.0, abs=0.01)
    assert result["inactive_days"] == 0


def test_activity_score_half_life_30_days():
    """30 天沒登入 → 分數掉到約一半（10 分）。驗證半衰期公式。"""
    from datetime import timedelta

    from core.identity import scoring

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (thirty_days_ago,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_activity_signals("EQidle30")

    # 半衰期 30 天 → e^(-ln2·30/30) = e^(-ln2) = 0.5 → 20 × 0.5 = 10
    assert result["score"] == pytest.approx(10.0, abs=0.1)
    assert result["inactive_days"] == 30


def test_activity_score_never_zero_exponential_asymptote():
    """指數衰減永不歸零——90 天仍有 ~2.5 分（非 0）。驗證「信任不一刀切」。"""
    from datetime import timedelta

    from core.identity import scoring

    ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (ninety_days_ago,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_activity_signals("EQidle90")

    # 3 個半衰期 → 20 × 0.5^3 = 2.5
    assert result["score"] == pytest.approx(2.5, abs=0.2)
    assert result["score"] > 0  # 永不歸零


def test_activity_score_never_active_returns_zero():
    """從未登入（last_active_at = NULL）→ 0 分。"""
    from core.identity import scoring

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (None,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_activity_signals("EQnever")

    assert result["score"] == 0
    assert result["reason"] == "never_active"


@pytest.mark.asyncio
async def test_recompute_no_user_id_returns_none():
    from core.identity import scoring
    assert await scoring.recompute_user_trust("") is None
    assert await scoring.recompute_user_trust(None) is None


def test_tier_from_score_boundaries():
    """分數 → tier 邊界對應 design doc。"""
    from core.identity.scoring import _tier_from_score

    assert _tier_from_score(0) == "anonymous"
    assert _tier_from_score(29) == "anonymous"
    assert _tier_from_score(30) == "known_wallet"
    assert _tier_from_score(59) == "known_wallet"
    assert _tier_from_score(60) == "soft_verified"
    assert _tier_from_score(79) == "soft_verified"
    assert _tier_from_score(80) == "strong_verified"
    assert _tier_from_score(100) == "strong_verified"


def test_collect_scam_penalty_clean_returns_zero():
    """scam DB 無命中 → penalty 0。"""
    from core.identity import scoring

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_scam_penalty("EQclean")

    assert result["penalty"] == 0
    assert result["reason"] == "clean"


def test_collect_scam_penalty_hit_returns_max():
    """scam DB 命中 → penalty 100。"""
    from core.identity import scoring

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)  # SELECT 1 命中
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(scoring, "get_connection", return_value=mock_conn):
        result = scoring.collect_scam_penalty("EQscam")

    assert result["penalty"] == 100
    assert result["reason"] == "scam_report_hit"


# ──────────────────────────────────────────────────────────────────────────────
# Router：分層揭露驗證
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_my_score_returns_breakdown_to_self(client):
    """GET /api/trust/score → 自己看到完整 breakdown。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQself123"})
    mocked_user = {
        "user_id": "EQself123", "username": "Self", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    fake_latest = {
        "trust_score": 45, "tier": "known_wallet",
        "breakdown": {"base_assessment": {"signals": []}, "scam_penalty": {"penalty": 0}},
        "computed_at": "2026-08-08T00:00:00Z", "recompute_reason": "scheduled",
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_latest_trust_score", return_value=fake_latest):
        response = await client.get(
            "/api/trust/score", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["trust_score"] == 45
    assert payload["tier"] == "known_wallet"
    assert payload["badge"]["badge_tier"] == 1
    assert "breakdown" in payload  # 自己看得到明細
    assert "next_steps" in payload


@pytest.mark.asyncio
async def test_get_user_badge_does_not_leak_breakdown(client):
    """GET /api/trust/tier/{user_id} → 別人只看到 tier+badge，沒有 breakdown（分層揭露）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQviewer"})
    mocked_user = {
        "user_id": "EQviewer", "username": "Viewer", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_user_trust_tier", return_value="soft_verified"):
        response = await client.get(
            "/api/trust/tier/EQtarget", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tier"] == "soft_verified"
    assert payload["badge"]["badge_tier"] == 2
    assert payload["badge"]["label_en"] == "Member"
    assert "breakdown" not in payload  # 分層揭露：別人看不到明細
    assert "trust_score" not in payload  # 也不看到實際分數


@pytest.mark.asyncio
async def test_get_score_first_access_triggers_recompute(client):
    """首次造訪（無歷史）→ 自動 recompute 一次。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQnew"})
    mocked_user = {
        "user_id": "EQnew", "username": "New", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    recompute_result = {"user_id": "EQnew", "trust_score": 30, "tier": "known_wallet", "breakdown": {}}
    fake_latest_after = {
        "trust_score": 30, "tier": "known_wallet", "breakdown": {"base_assessment": {"signals": []}},
        "computed_at": "2026-08-08T00:00:00Z", "recompute_reason": "first_access",
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_latest_trust_score",
               side_effect=[None, fake_latest_after]), \
         patch("api.routers.trust.recompute_user_trust",
               new=AsyncMock(return_value=recompute_result)):
        response = await client.get(
            "/api/trust/score", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recompute_reason"] == "first_access"


@pytest.mark.asyncio
async def test_recompute_endpoint_returns_score(client):
    """POST /api/trust/recompute → 重算回 score/tier。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.recompute_user_trust",
               new=AsyncMock(return_value={"user_id": "EQuser", "trust_score": 50, "tier": "known_wallet"})):
        response = await client.post(
            "/api/trust/recompute", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["trust_score"] == 50
    assert payload["tier"] == "known_wallet"


@pytest.mark.asyncio
async def test_disabled_feature_returns_503(client):
    """TRUST_SCORE_ENABLED=false → 503。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.TRUST_SCORE_ENABLED", False):
        response = await client.get(
            "/api/trust/score", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 503


# ──────────────────────────────────────────────────────────────────────────────
# 舊資料修復：_breakdown_has_old_unknown（7/29-8/9 版本把無鏈上紀錄寫成 "unknown"）
# ──────────────────────────────────────────────────────────────────────────────


def test_breakdown_has_old_unknown_detects_legacy_detail():
    """舊格式 detail="unknown" → 偵測到,應觸發自動重算。"""
    from api.routers.trust import _breakdown_has_old_unknown

    breakdown = {
        "base_assessment": {
            "signals": [
                {"name": "wallet_ownership", "score": 30, "detail": "TON proof verified"},
                {"name": "wallet_age", "score": 0, "detail": "unknown"},
                {"name": "wallet_activity", "score": 0, "detail": "unknown"},
            ]
        }
    }
    assert _breakdown_has_old_unknown(breakdown) is True


def test_breakdown_new_format_not_detected():
    """新格式(missing_history / 實際天數)→ 不需重算。"""
    from api.routers.trust import _breakdown_has_old_unknown

    breakdown = {
        "base_assessment": {
            "signals": [
                {"name": "wallet_age", "score": 0, "detail": "missing_history"},
                {"name": "wallet_activity", "score": 5, "detail": "50 txs"},
            ]
        }
    }
    assert _breakdown_has_old_unknown(breakdown) is False


def test_breakdown_empty_or_missing_not_detected():
    """空 breakdown / 缺 signals → 不誤判。"""
    from api.routers.trust import _breakdown_has_old_unknown

    assert _breakdown_has_old_unknown(None) is False
    assert _breakdown_has_old_unknown({}) is False
    assert _breakdown_has_old_unknown({"base_assessment": {}}) is False
    assert _breakdown_has_old_unknown({"base_assessment": {"signals": []}}) is False
