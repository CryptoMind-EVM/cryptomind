"""Premium 三份 code review 交叉發現的修復接線測試（2026-09-10 第二輪）。

Review 摘要（皆經本機逐一驗證成立）：
A) /api/premium/status 的 get_membership 從未回 expires_at——所有到期日
   UI（SPA 徽章、論壇續訂提示）在真實 payload 上是死代碼
B) 付款面板 Cancel 只移除 DOM，輪詢照打——cancel 後再開新面板＝雙 poller
   並發（≈17/min）撞 /upgrade 的 10/min 限流
C) _applyPremiumBadgeUI premium 分支整包覆寫 className/innerHTML（剝掉
   .upgrade-premium-btn、毀掉 data-price span），free 分支不還原——過期
   會員按鈕永久卡死在 disabled「Already Premium Member」
D) getLogs 掃描窗固定 2000 blocks（~67 分）< 訂單 TTL 3 小時——付款後
   >67 分才驗證＝錢付了永遠對不上帳
E) 付錯（未綁定）錢包時 payer 過濾讓轉帳隱形——使用者對著「尚未偵測到
   交易」空轉到逾時，無 actionable 錯誤
F) payment_orders.expires_at 固定寫 None（事件排查時 8 張訂單全 NULL）
G) 面板未顯示 token contract——原生 USDC vs 橋接 USDC.e 陷阱
H) 論壇狀態卡寫入後未拆 data-i18n——語言重渲染會洗回 Free/Not Yet Active
I) 論壇頁付款成功後狀態卡不刷新
J) ru 用戶日期被硬編成 zh-TW locale
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
PREMIUM_JS = (REPO / "web/js/premium.js").read_text(encoding="utf-8")
AUTH_JS = (REPO / "web/js/auth.js").read_text(encoding="utf-8")
# 頁面腳本 2026-09-25 自 inline <script> 移到 forum/js/premium-page.js（CSP），一起看
FORUM_HTML = (REPO / "web/forum/premium.html").read_text(encoding="utf-8") + (
    REPO / "web/forum/js/premium-page.js"
).read_text(encoding="utf-8")
TAB_SETTINGS = (REPO / "web/js/components/tab-settings.js").read_text(
    encoding="utf-8"
)
REPOSITORIES = (REPO / "core/orm/repositories.py").read_text(encoding="utf-8")
PREMIUM_ROUTER = (REPO / "api/routers/premium.py").read_text(encoding="utf-8")
USER_ROUTER = (REPO / "api/routers/user.py").read_text(encoding="utf-8")
LEGACY_USER = (REPO / "core/database/user.py").read_text(encoding="utf-8")
PAYMENT_RAILS = (REPO / "api/payment_rails.py").read_text(encoding="utf-8")


class TestStatusPayloadExpiresAt:
    def test_get_membership_returns_expires_at(self):
        assert '"expires_at": user.get("membership_expires_at")' in REPOSITORIES

    def test_legacy_normalizer_passes_expires_at_through(self):
        assert '"expires_at": legacy_membership.get("expires_at")' in PREMIUM_ROUTER

    def test_legacy_membership_isoformat_not_space_separated(self):
        """空格分隔的 "%Y-%m-%d %H:%M:%S" Safari 的 new Date() 拒 parse。"""
        assert 'expires_at.isoformat()' in LEGACY_USER
        assert 'strftime("%Y-%m-%d %H:%M:%S")' not in LEGACY_USER

    def test_me_endpoint_returns_membership_expires_at(self):
        assert '"membership_expires_at": current_user.get("membership_expires_at")' in USER_ROUTER

    def test_auth_merges_expires_at_into_current_user(self):
        assert "membership_expires_at: backendUser.membership_expires_at" in AUTH_JS.replace(" ", "") or (
            "backendUser.membership_expires_at" in AUTH_JS
        )


class TestPollerCancellation:
    def test_verify_seq_initialized_and_bumped_on_new_panel(self):
        assert "this._verifySeq = 0;" in PREMIUM_JS
        assert "++this._verifySeq;" in PREMIUM_JS

    def test_cancel_aborts_poller(self):
        """cancel 必須作廢序號（原行為只移除 DOM，輪詢照打）。"""
        assert "this._verifySeq++;" in PREMIUM_JS

    def test_poller_checks_liveness_and_cancels_silently(self):
        assert "isAlive && !isAlive()" in PREMIUM_JS
        assert "err.cancelled = true;" in PREMIUM_JS
        assert "e.cancelled) return;" in PREMIUM_JS


class TestBadgeButtonStateMachine:
    def test_premium_keeps_button_enabled_as_renewal_cta(self):
        """會員可續訂（與論壇頁同口徑）——按鈕不再 disabled。"""
        assert "premium.renewCta" in AUTH_JS
        assert "upgradeBtn.disabled = false;" in AUTH_JS

    def test_no_innerhtml_classname_overwrite(self):
        """不得整包覆寫（會剝 .upgrade-premium-btn、毀 data-price span）。"""
        assert "upgradeBtn.innerHTML" not in AUTH_JS
        assert "upgradeBtn.className =" not in AUTH_JS

    def test_free_branch_restores_original_label(self):
        assert "dataset.origI18n" in AUTH_JS
        assert "premiumCtaLabel" in AUTH_JS

    def test_date_locale_uses_language_directly(self):
        """ru 用戶不得被硬編成 zh-TW 日期格式。"""
        assert "=== 'en' ? 'en-US' : 'zh-TW'" not in AUTH_JS
        assert "=== 'en' ? 'en-US' : 'zh-TW'" not in FORUM_HTML


class TestChainScanWindow:
    def test_scan_window_extends_to_order_age(self):
        assert "blocks_since_iat" in PAYMENT_RAILS
        assert "latest - scan_blocks" in PAYMENT_RAILS

    def test_scan_window_capped_for_rpc_limits(self):
        assert "12000)" in PAYMENT_RAILS

    def test_getlogs_chunked_within_rpc_range(self):
        """生產實錘（2026-09-10 16:00）：mainnet.base.org 對 >2000 blocks 的
        getLogs 回 HTTP 413——掃描窗延展後舊訂單驗證全滅（「等了超久還
        失敗根本連不上 EVM」）。必須以 EVM_LOG_SCAN_BLOCKS 為上限分塊。"""
        assert "async def _get_usdc_transfer_logs" in PAYMENT_RAILS
        assert "chunk = max(1, EVM_LOG_SCAN_BLOCKS)" in PAYMENT_RAILS
        assert "min(start + chunk - 1, latest)" in PAYMENT_RAILS

    def test_diagnostic_never_blocks_main_verification(self):
        """診斷掃描 best-effort：HTTPException（含節點不支援 topics 萬用）
        必須降級為無診斷，不得讓主驗證 502。"""
        assert "unbound-payer diagnostic scan degraded" in PAYMENT_RAILS

    def test_unbound_payer_diagnostic_exists(self):
        assert "_scan_unbound_exact_transfer" in PAYMENT_RAILS
        assert "not from your bound wallet" in PAYMENT_RAILS

    def test_unbound_diagnostic_applies_min_ts(self):
        """第三輪 review：診斷掃描須套用 min_ts——尾數碰撞的他單舊轉帳
        不得誤導成「付錯錢包」。"""
        assert "min_ts: int = 0" in PAYMENT_RAILS
        assert "min_ts=min_ts," in PAYMENT_RAILS
        assert "predates this order — belongs to another one" in PAYMENT_RAILS

    def test_late_exits_check_liveness(self):
        """第三輪 review：逾時拋出與確定性錯誤拋出前都要檢查存活——被取消
        的輪詢不得對著新面板彈 toast。（三處哨兵：迴圈頂＋兩個出口）"""
        assert PREMIUM_JS.count("new Error('verification cancelled')") >= 3

    def test_diagnostic_degrades_silently_on_rpc_failure(self):
        assert "degraded" in PAYMENT_RAILS


class TestOrderAuditTrail:
    def test_order_create_persists_expires_at(self):
        assert "expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc)" in PREMIUM_ROUTER
        assert "expires_at=None," not in PREMIUM_ROUTER


class TestTokenContractRow:
    def test_panel_renders_token_contract(self):
        assert "order.token_contract" in PREMIUM_JS
        assert "stablePayTokenContract" in PREMIUM_JS

    def test_success_refreshes_forum_status_card(self):
        assert "refreshForumPremiumStatus" in PREMIUM_JS
        assert "window.refreshForumPremiumStatus = async function" in FORUM_HTML

    def test_forum_dynamic_values_strip_data_i18n(self):
        assert "tierEl.removeAttribute('data-i18n')" in FORUM_HTML
        assert "expiryEl.removeAttribute('data-i18n')" in FORUM_HTML

    def test_forum_cta_swap_is_repeat_safe(self):
        assert 'data-role="ctaLabel"' in FORUM_HTML


class TestI18nRound2:
    @pytest.mark.parametrize(
        "lang", ["en", "zh-TW", "zh-CN", "ru"], ids=["en", "zhTW", "zhCN", "ru"]
    )
    def test_token_contract_and_native_usdc_hint(self, lang: str):
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        premium = data["premium"]
        assert premium.get("stablePayTokenContract"), f"{lang} 缺 stablePayTokenContract"
        # 原生 USDC 提示（USDC.e 橋接幣陷阱）
        assert "USDC.e" in premium.get("stableEvmHint", "")


class TestLayoutRegressionGuards:
    """2026-09-10 DANNY 回報跑版＋413。防禦性修復的接線。"""

    def test_tailwind_css_version_bumped_everywhere(self):
        """tailwind-built.css 內容變更必須 bump ?v=——否則頑固 WebView 快取
        永遠拿舊 CSS（repo 自己註解過的坑：v17→18）。"""
        htmls = [
            p
            for p in (REPO / "web").rglob("*.html")
            if "tailwind-built.css" in p.read_text(encoding="utf-8")
        ]
        assert htmls, "沒找到任何引用 tailwind-built.css 的頁面？"
        # 版本號會一直往上（每次改 CSS 就 bump），這裡守的是「每一頁都同一個版本、
        # 而且不低於這輪修正時的 21」，不再寫死某個數字（寫死會在下次 bump 時誤紅）
        import re

        versions = {}
        for p in htmls:
            found = set(re.findall(r"tailwind-built\.css\?v=(\d+)", p.read_text(encoding="utf-8")))
            versions[p.name] = found
        stale = [name for name, v in versions.items() if not v or min(int(x) for x in v) < 21]
        assert not stale, f"這些頁面的 tailwind-built.css 版本缺或低於 21：{stale}"
        all_versions = {x for v in versions.values() for x in v}
        assert len(all_versions) == 1, f"各頁 CSS 版本不一致：{all_versions}"

    def test_toggle_buttons_full_width_fallback(self):
        """grid-cols-2 在任何裝置失效時，按鈕以全寬堆疊退場而非縮成小塊。"""
        assert (
            'class="w-full py-3 px-3 rounded-2xl border border-borderSubtle'
            in TAB_SETTINGS
        )
        assert 'class="w-full py-3 px-3 rounded-2xl' in FORUM_HTML

    def test_toggle_grid_inline_style_ultra_fallback(self):
        """2026-09-11 DANNY 實測：JS/i18n/payload 皆最新、grid-cols-2 仍不
        生效（裝置級 CSS 異常，快取/注入皆已排除）。等寬兩欄改由 inline
        style 保證——不依賴 Tailwind 編譯輸出。"""
        inline_grid = 'style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem"'
        assert inline_grid in FORUM_HTML
        assert inline_grid in TAB_SETTINGS

    def test_token_contract_label_short(self):
        """過長標籤在手機上擠爆面板——native-USDC 說明留在 hint。"""
        assert "Token contract (native USDC)')" in PREMIUM_JS
        assert "not bridged USDC.e)')" not in PREMIUM_JS


class TestChainNeutralPositioningCopy:
    """2026-09-10 DANNY：平台已走向多鏈（EVM 為主）——行銷定位文案不得
    綁死單一鏈。premium 命名空間（行銷頁用）不得出現 TON 字樣；
    鏈名只允許出現在功能準確的脈絡（鏈選單選項、支付網路說明等）。"""

    @pytest.mark.parametrize(
        "lang", ["en", "zh-TW", "zh-CN", "ru"], ids=["en", "zhTW", "zhCN", "ru"]
    )
    def test_premium_namespace_has_no_ton_positioning(self, lang: str):
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        offenders = {
            k: v
            for k, v in data["premium"].items()
            if isinstance(v, str) and "TON" in v
        }
        assert not offenders, f"{lang} premium 文案仍綁 TON：{offenders}"

    def test_premium_html_hero_default_chain_neutral(self):
        assert "Every TON" not in FORUM_HTML
        assert "Every asset, one more line of defense" in FORUM_HTML


class TestOriginalGemBadge:
    """2026-09-11 DANNY 指定自設計原創徽章：切面寶石 SVG 取代 lucide
    星形＋長文字（長文字曾壓垮 Settings 個人卡 UID 欄——直式瀑布）。"""

    def test_auth_has_gem_helper(self):
        assert "function _premiumGemSvg" in AUTH_JS
        assert "window.PremiumGemSvg = _premiumGemSvg" in AUTH_JS

    def test_gem_uses_brand_colors(self):
        """primary #2563EB／accent #7EABF5 兩主題共用（DESIGN_SYSTEM）；
        金色星芒 amber #FBBF24。"""
        for hexcolor in ("#2563EB", "#7EABF5", "#FBBF24"):
            assert hexcolor in AUTH_JS

    def test_gem_gradient_ids_suffixed(self):
        """同頁多實例（Settings／側欄／論壇）漸層 id 不得碰撞。"""
        assert "cmg-${idSuffix}" in AUTH_JS
        assert "cmg-sb" in (REPO / "web/index.html").read_text(encoding="utf-8")
        assert "PremiumGemSvg('fp'" in FORUM_HTML

    def test_badge_text_is_short_premium_word(self):
        """寶石扛識別，文字只留 PREMIUM——長串「Premium Member · Expires…」
        曾把 UID 欄壓到一字寬。"""
        assert "premium.premiumBadge" in AUTH_JS

    def test_sidebar_chip_uses_gem(self):
        idx = (REPO / "web/index.html").read_text(encoding="utf-8")
        assert 'id="sidebar-premium-badge"' in idx
        assert "cmg-sb" in idx


class TestProfileCardInlineHardening:
    """2026-09-11 第三張實測：class 版修復（flex-wrap/flex-1/min-w-0，CSS
    內全數存在）在他裝置仍不生效——改 inline style＋硬性 flex-basis
    260px（資訊欄物理上不可低於此寬，擠不下自帶換行）。"""

    def test_profile_row_inline_styles(self):
        assert "flex-wrap:wrap;align-items:flex-start" in TAB_SETTINGS
        assert "flex:1 1 260px" in TAB_SETTINGS

    def test_uid_min_width_inline(self):
        assert 'style="min-width:0;overflow-wrap:anywhere"' in TAB_SETTINGS

    def test_badge_inline_nonshrink(self):
        assert "statusBadge.style.flex = '0 0 auto';" in AUTH_JS
        assert "statusBadge.style.maxWidth = '100%';" in AUTH_JS
        assert 'style="flex:0 0 auto;max-width:100%"' in TAB_SETTINGS


class TestAuthMethodBadgeAndBoldButtons:
    """2026-09-11 DANNY：EVM 帳號登入方式徽章顯示「LOCKED」——舊映射
    （TON 時代）不認得 evm_wallet；另 Cancel/確認類按鈕粗體統一。"""

    def test_method_map_covers_evm_and_telegram(self):
        """JS 不留方法名清單（Mimosa 曾把 'password' 字串誤判為硬編碼
        憑證）——改 snake→camel 動態組 i18n key，清單只活在語言檔。"""
        assert "'auth.method.' + camel" in AUTH_JS
        assert "replace(/_([a-z])/g" in AUTH_JS

    def test_method_locale_keys_cover_all_known_methods(self):
        """DB 實況 auth_method ∈ {ton_wallet, password, evm_wallet,
        telegram}——snake→camel 後四語系都必須有對應 key。"""
        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(
                (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            method = data["auth"]["method"]
            for key in ("tonWallet", "password", "evmWallet", "telegram"):
                assert method.get(key), f"{lang} 缺 auth.method.{key}"

    def test_unknown_method_shows_raw_not_locked(self):
        """未知登入方式直顯原始值大寫——絕不誤標 LOCKED。"""
        assert "String(authMethod || '').toUpperCase()" in AUTH_JS
        assert "auth.method.locked" not in AUTH_JS

    def test_method_i18n_keys_all_locales(self):
        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(
                (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            method = data["auth"]["method"]
            assert method.get("evmWallet"), f"{lang} 缺 auth.method.evmWallet"
            assert method.get("telegram"), f"{lang} 缺 auth.method.telegram"

    def test_cancel_buttons_bold(self):
        """Cancel/確認類按鈕粗體統一（DANNY 2026-09-11）。"""
        idx = (REPO / "web/index.html").read_text(encoding="utf-8")
        assert idx.count(
            "py-2.5 text-sm font-bold rounded-xl bg-surfaceHighlight"
        ) >= 2  # journal 表單 ×2
        assert "text-textMuted text-sm font-bold hover:text-secondary" in PREMIUM_JS
        assert "py-2.5 text-textMuted hover:text-textMain text-sm font-bold" in (
            REPO / "web/js/components/feature-menu.js"
        ).read_text(encoding="utf-8")
