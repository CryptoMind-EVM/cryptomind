// forum/premium.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

// 初始化頁面
document.addEventListener('DOMContentLoaded', async () => {
    const backLink = document.getElementById('forum-premium-back-link');
    if (backLink) {
        backLink.href = sessionStorage.getItem('forumBackHref') || '/static/index.html#forum';
    }

    // 初始化認證
    if (typeof initializeAuth === 'function') {
        await initializeAuth();
    }

    // 加載用戶會員狀態（DOMContentLoaded 與付款成功後共用——
    // window.refreshForumPremiumStatus 由 premium.js 在驗證成功後呼叫）
    window.refreshForumPremiumStatus = async function () {
        if (!window.AuthManager?.currentUser) return;
        const userId = window.AuthManager.currentUser.user_id || window.AuthManager.currentUser.uid;
        if (!userId) return;
        try {
            const result = await AppAPI.get(`/api/premium/status`);

            if (result.success) {
                const membership = result.membership;
                const isPremium = membership.is_premium;
                // 動態值寫入時拆掉 data-i18n——否則任何 i18n 重渲染
                // （語言切換／登入後 applyServerLanguage）會把會員的
                // 狀態卡洗回「Free Member / Not Yet Active」預設文案
                const tierEl = document.getElementById('current-tier');
                tierEl.removeAttribute('data-i18n');
                tierEl.textContent = window.I18n ? window.I18n.t(isPremium ? 'auth.premiumMember' : 'auth.freeMember') : (isPremium ? 'Premium Member' : 'Free Member');

                const badge = document.getElementById('membership-badge');
                // 會員＝原創寶石徽章（與 SPA 側欄／Settings 同一顆）；
                // FREE 維持純文字。PremiumGemSvg 由 auth.js 提供。
                const gemSvg = isPremium && typeof window.PremiumGemSvg === 'function'
                    ? window.PremiumGemSvg('fp', 'w-3 h-3 inline-block')
                    : '';
                badge.innerHTML = `${gemSvg}<span>${isPremium ? (window.I18n ? window.I18n.t('premium.premiumBadge') : 'PREMIUM') : (window.I18n ? window.I18n.t('premium.freeBadge') : 'FREE')}</span>`;
                badge.className = isPremium
                    ? 'px-2 py-0.5 rounded-md bg-primary/10 text-[10px] text-primary border border-primary/20 uppercase'
                    : 'px-2 py-0.5 rounded-md bg-surfaceHighlight text-[10px] text-textMuted border border-borderSubtle uppercase';

                // 到期日：格式化為本地日期——原本直出 ISO 微秒字串
                // （2026-11-10T08:10:25.795422+00:00）沒人看得懂。
                // locale 直用語系代碼（en/zh-TW/zh-CN/ru 皆合法 BCP-47）
                const locale = window.I18n?.getLanguage?.() || 'zh-TW';
                const expiryDate = membership.expires_at
                    ? new Date(membership.expires_at).toLocaleDateString(locale)
                    : null;
                const expiryEl = document.getElementById('expiry-date');
                expiryEl.removeAttribute('data-i18n');
                expiryEl.textContent = expiryDate || (window.I18n ? window.I18n.t(isPremium ? 'premium.accessActive' : 'premium.notActivated') : (isPremium ? 'Access active' : 'Not activated'));

                // 會員情境：CTA 換續訂文案＋正下方顯示目前到期日
                // （再次購買＝自到期日順延，不是重新買一個月）。
                // data-role 標記讓「拆掉 data-i18n 之後」的重複呼叫
                // （付款成功後再刷新）仍找得到同一個 span，且 free 可還原。
                const ctaSpan = document.querySelector('#upgrade-btn span[data-i18n]')
                    || document.querySelector('#upgrade-btn span[data-role="ctaLabel"]');
                if (ctaSpan) {
                    if (!ctaSpan.dataset.origI18n) {
                        ctaSpan.dataset.origI18n = ctaSpan.getAttribute('data-i18n') || '';
                    }
                    const inline = document.getElementById('premium-inline-status');
                    if (isPremium && expiryDate) {
                        ctaSpan.setAttribute('data-role', 'ctaLabel');
                        ctaSpan.removeAttribute('data-i18n');
                        ctaSpan.textContent = window.I18n
                            ? window.I18n.t('premium.renewCta') || 'Renew Premium'
                            : 'Renew Premium';
                        if (inline) {
                            inline.textContent = window.I18n
                                ? window.I18n.t('premium.currentUntil', { date: expiryDate }) || `Premium · expires ${expiryDate}`
                                : `Premium · expires ${expiryDate}`;
                            inline.classList.remove('hidden');
                        }
                    } else {
                        if (ctaSpan.dataset.origI18n) {
                            ctaSpan.setAttribute('data-i18n', ctaSpan.dataset.origI18n);
                            ctaSpan.removeAttribute('data-role');
                            ctaSpan.textContent = window.I18n
                                ? window.I18n.t(ctaSpan.dataset.origI18n) || ctaSpan.textContent
                                : ctaSpan.textContent;
                        }
                        if (inline) inline.classList.add('hidden');
                    }
                }
            }
        } catch (error) {
            console.error('獲取會員狀態失敗:', error);
        }
    };
    await window.refreshForumPremiumStatus();

    // 更新用戶名顯示
    if (window.AuthManager?.currentUser) {
        document.querySelector('.user-display-name').textContent = window.AuthManager.currentUser.username;
    }
});
