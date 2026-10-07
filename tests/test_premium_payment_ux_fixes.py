"""Premium 付款 UX 修復的接線測試（2026-09-10 DANNY 回報三張截圖排查）。

事件背景（生產證據）：論壇 premium 頁對「已是會員」毫無標示，CTA 一律
「Start Protection」；會員點了 → 付款面板 → 錢包付款失敗（鏈上 0 筆）
→「我已付款」輪詢 3 秒×12 次自己撞上 /upgrade 的 10/min 限流（429），
期間按鈕只有「Verifying…」無進度無原因，最後吐出限流/逾時錯誤；
且重複點擊會疊出多張 overlay（每張各帶一張訂單，金額尾數互不相符）。

本檔以 source-grep 鎖住修復接線（依 tests/AGENTS.md 慣例，不引入前端
單測框架）：
- premium.js：輪詢節奏 7 秒×15 次、429 退避、確定性錯誤正則含 bound/
  predates、輪詢進度回呼、overlay 去重、方案切換改 document 委派
  （tab 重渲染存活）、data-plan-note 同步
- forum premium.html：會員 CTA 換續訂文案、到期日 inline 顯示、
  到期日本地化格式化（不再直出 ISO 微秒字串）
- tab-settings.js：月/年方案切換進 SPA Settings 卡
- auth.js：快取首繪即帶 membership_expires_at（不用等 API 刷新）
- i18n ×4：新增 key 齊備（中文只在 locale JSON，JS fallback 一律英文）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
PREMIUM_JS = (REPO / "web/js/premium.js").read_text(encoding="utf-8")
# 頁面腳本 2026-09-25 自 inline <script> 移到 forum/js/premium-page.js（CSP），一起看
FORUM_HTML = (REPO / "web/forum/premium.html").read_text(encoding="utf-8") + (
    REPO / "web/forum/js/premium-page.js"
).read_text(encoding="utf-8")
TAB_SETTINGS = (REPO / "web/js/components/tab-settings.js").read_text(encoding="utf-8")
AUTH_JS = (REPO / "web/js/auth.js").read_text(encoding="utf-8")


class TestVerifierPolling:
    def test_poll_interval_and_attempts_within_rate_limit(self):
        """/api/premium/upgrade 限 10/min——3 秒×12 次（12 次/40 秒）會自撞
        429；改為 7 秒×15 次（≈8.6/min）全程在限流內且涵蓋錢包送款時間。"""
        assert "const TOTAL = 15;" in PREMIUM_JS
        assert "setTimeout(r, 7000)" in PREMIUM_JS
        assert "setTimeout(r, 3000)" not in PREMIUM_JS

    def test_429_backoff_not_treated_as_failure(self):
        """429 是自家輪詢撞限流：退避後繼續，不得記成失敗原因。"""
        assert "e.status === 429" in PREMIUM_JS
        assert "setTimeout(r, 9000)" in PREMIUM_JS

    def test_deterministic_error_regex_covers_backend_wording(self):
        """後端實際文案用「bound/bind your」（未綁錢包）與「predates」
        （訂單過期）——原正則只比對 belong 比不中，會靜默重試到逾時。"""
        assert "bound|bind your|predates" in PREMIUM_JS

    def test_poll_progress_callback_wired(self):
        """每次輪詢回報進度（Verifying (n/15)…），不再只有無進度的 Verifying…。"""
        assert "onProgress(i + 1, TOTAL)" in PREMIUM_JS
        assert "verifyingAttempt" in PREMIUM_JS
        assert "{{current}}" in PREMIUM_JS

    def test_overlay_dedupe_before_new_panel(self):
        """重複點擊不得疊出多張付款面板（多訂單金額尾數互不相符，付了也對不上）。"""
        assert "getElementById('stable-pay-overlay')" in PREMIUM_JS


class TestPlanToggleAndNote:
    def test_plan_toggle_delegated_on_document(self):
        """SPA settings 分頁會重渲染卡片——per-button 監聽器隨舊 DOM 失效，
        方案切換必須走 document 層委派。"""
        assert "document.addEventListener('click'" in PREMIUM_JS
        assert "closest('[data-plan-toggle]')" in PREMIUM_JS

    def test_plan_note_syncs_with_selected_plan(self):
        assert "data-plan-note" in PREMIUM_JS
        assert "premium.yearlyNote" in PREMIUM_JS
        assert "premium.monthlyNote" in PREMIUM_JS

    def test_spa_settings_card_has_monthly_yearly_toggle(self):
        """方案選擇原本只在論壇 premium 頁——SPA Settings 付款入口也要能選。"""
        assert TAB_SETTINGS.count('data-plan-toggle="premium_monthly"') == 1
        assert TAB_SETTINGS.count('data-plan-toggle="premium_yearly"') == 1
        assert "data-plan-note" in TAB_SETTINGS


class TestForumPremiumMemberState:
    def test_inline_status_element_near_cta(self):
        """會員狀態卡在頁尾，手機上看不到——CTA 正下方要有 inline 狀態行。"""
        assert 'id="premium-inline-status"' in FORUM_HTML

    def test_member_cta_swaps_to_renew(self):
        assert "premium.renewCta" in FORUM_HTML

    def test_current_until_with_date_shown(self):
        assert "premium.currentUntil" in FORUM_HTML

    def test_expiry_date_localized_not_raw_iso(self):
        """到期日不得直出 ISO 微秒字串（2026-11-10T08:10:25.795422+00:00）。"""
        assert "toLocaleDateString" in FORUM_HTML


class TestCachedExpiryDisplay:
    def test_cached_first_paint_carries_expiry(self):
        """快取首繪（API 刷新前）就要顯示到期日，不得只顯示 Premium Member。"""
        assert "membership_expires_at || null" in AUTH_JS


class TestI18nKeys:
    @pytest.mark.parametrize(
        "lang", ["en", "zh-TW", "zh-CN", "ru"], ids=["en", "zhTW", "zhCN", "ru"]
    )
    def test_new_premium_keys_exist(self, lang: str):
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        premium = data["premium"]
        for key in (
            "verifyingAttempt",
            "renewCta",
            "currentUntil",
            "monthlyNote",
            "yearlyNote",
        ):
            assert key in premium and premium[key], f"{lang} 缺 premium.{key}"
        assert "{{current}}" in premium["verifyingAttempt"]
        assert "{{date}}" in premium["currentUntil"]

    def test_js_fallbacks_english_only(self):
        """JS 內嵌 fallback 一律英文（中文只活在 locale JSON）。"""
        for snippet in (
            "Verifying ({{current}}/{{total}})…",
            "Renew Premium",
            "Monthly plan (30 days). Renew manually before expiry.",
            "Yearly plan (365 days). Renew manually before expiry.",
        ):
            assert snippet in PREMIUM_JS or snippet in FORUM_HTML

    def test_cta_buttons_have_horizontal_padding(self):
        """2026-09-11 DANNY：續訂按鈕閃電圖示貼邊——CTA 一律要有水平內距
        （只有 py 的全寬按鈕，長文案時 icon 會疊到邊框上）。"""
        for snippet in (
            'class="w-full px-4 py-3.5 bg-primary hover:bg-primary/90',
            'w-full px-4 py-4 bg-primary hover:bg-primary/90',
        ):
            assert snippet in TAB_SETTINGS or snippet in PREMIUM_JS or snippet in FORUM_HTML
