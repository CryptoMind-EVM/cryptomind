"""會員到期 = 有效等級失效（2026-09-10 DANNY「到期後是否仍顯示訂閱」徹查）。

核心語義：對外輸出的 membership_tier 必須是「有效等級」——過期用戶的 tier
就是 free。若只在 is_premium 翻 false 而 tier 字串保留 "premium"，所有讀
current_user.get("membership_tier") 的 Premium gate（analysis /
agent_presets / agent_configs 共 14+ 處）在到期後會繼續放行，前端
auth.js 的快取 badge 也會先閃「已訂閱」。

另覆蓋 Settings 卡價格修復的前端接線：SPA 裡只有 forum tab 會觸發
loadPiPrices，直達 Settings 時 data-price 佔位（spinner）永不填充
（DANNY 回報「移除會員後刷新，Upgrade 按鈕一直卡 Loading」）。
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _make_user(**overrides):
    from core.orm.models import User

    kwargs = dict(
        user_id="ent-test-1",
        username="EntUser",
        is_active=True,
    )
    kwargs.update(overrides)
    return User(**kwargs)


class TestUserToDictEffectiveTier:
    """_user_to_dict 的 tier 必須反映有效等級（過期即 free）。"""

    def test_expired_premium_downgrades_tier(self):
        from core.orm.repositories import _user_to_dict

        user = _make_user(
            membership_tier="premium",
            membership_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        result = _user_to_dict(user)
        assert result["is_premium"] is False
        assert result["membership_tier"] == "free", "過期的 premium 不得以 tier 字串繼續過 gate"

    def test_unexpired_premium_keeps_tier(self):
        from core.orm.repositories import _user_to_dict

        user = _make_user(
            membership_tier="premium",
            membership_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        result = _user_to_dict(user)
        assert result["is_premium"] is True
        assert result["membership_tier"] == "premium"

    def test_no_expiry_never_expires(self):
        from core.orm.repositories import _user_to_dict

        user = _make_user(membership_tier="premium", membership_expires_at=None)
        result = _user_to_dict(user)
        assert result["is_premium"] is True
        assert result["membership_tier"] == "premium"

    def test_legacy_pro_without_expiry_normalizes_premium(self):
        from core.orm.repositories import _user_to_dict

        user = _make_user(membership_tier="pro", membership_expires_at=None)
        result = _user_to_dict(user)
        assert result["is_premium"] is True
        assert result["membership_tier"] == "premium"

    def test_naive_expiry_datetime_treated_as_utc(self):
        """DB 可能回 naive datetime；不得因比較 TypeError 誤判成未過期。"""
        from core.orm.repositories import _user_to_dict

        user = _make_user(
            membership_tier="premium",
            membership_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=1),
        )
        result = _user_to_dict(user)
        assert result["is_premium"] is False
        assert result["membership_tier"] == "free"


class TestLegacyMembershipFallback:
    """premium /status 的 legacy fallback 不得把過期 tier 原樣回給前端。"""

    def test_legacy_expired_tier_normalized_to_free(self):
        from api.routers.premium import _normalize_legacy_membership

        out = _normalize_legacy_membership(
            {"tier": "premium", "is_premium": False, "is_expired": True}
        )
        assert out["is_premium"] is False
        assert out["membership_tier"] == "free"

    def test_legacy_active_premium_kept(self):
        from api.routers.premium import _normalize_legacy_membership

        out = _normalize_legacy_membership(
            {"tier": "premium", "is_premium": True, "is_expired": False}
        )
        assert out["is_premium"] is True
        assert out["membership_tier"] == "premium"


class TestSettingsPricingWiring:
    """Settings 價格：SPA 直達 settings 也要觸發 /api/premium/pricing 載入。"""

    def test_spa_settings_loader_triggers_price_load(self):
        src = (REPO / "web" / "js" / "spa.js").read_text(encoding="utf-8")
        block = re.search(r"settings: \(\) =>.*?(?=\n\};)", src, re.S)
        assert block, "找不到 _TAB_MODULES.settings 載入器"
        assert "loadPiPrices" in block.group(0), (
            "settings 載入器要冪等觸發 loadPiPrices——只有 forum tab 會載入的話，"
            "直達 Settings 的 data-price spinner 永不填充"
        )

    def test_load_pi_prices_still_exported(self):
        src = (REPO / "web" / "js" / "forum-config.js").read_text(encoding="utf-8")
        assert re.search(r"export \{[^}]*\bloadPiPrices\b", src), (
            "loadPiPrices 需維持 export（spa.js settings 載入器依賴它）"
        )

    def test_settings_upgrade_button_has_price_placeholder(self):
        """鎖定 UI 契約：升級鈕的價格就是 data-price="premium" 佔位，由定價填充。"""
        src = (REPO / "web" / "js" / "components" / "tab-settings.js").read_text(
            encoding="utf-8"
        )
        assert 'data-price="premium"' in src
