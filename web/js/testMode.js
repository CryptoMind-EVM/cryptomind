// ========================================
// testMode.js - 測試模式功能
// ========================================

/**
 * 初始化測試模式 UI
 */
async function initTestMode() {
    const tierSwitcher = document.getElementById('test-tier-switcher');
    // 切換帳號區塊也在設定頁：auth 初始化時設定頁常還沒注入，要在這裡同步
    const userSwitcher = document.getElementById('dev-user-switcher');
    if (window.PiEnvironment?.shouldBlockProtectedRequests()) {
        if (tierSwitcher) {
            tierSwitcher.classList.add('hidden');
        }
        userSwitcher?.classList.add('hidden');
        return;
    }

    // 用共用的 getAppConfig（有快取）判斷 test_mode，避免時序問題
    // （__APP_TEST_MODE 全域變數由 auth.js async 設定，可能還沒設好）。
    // production（TEST_MODE=false）直接隱藏切換器並跳過 current-tier API 呼叫，
    // 避免 /api/test-mode/current-tier 必定 403 在每個使用者 console 噴 error。
    let isTestMode = false;
    try {
        if (typeof AppAPI !== 'undefined' && AppAPI.getAppConfig) {
            const cfg = await AppAPI.getAppConfig();
            isTestMode = !!cfg?.test_mode;
        }
    } catch (e) {
        // config 抓不到，保守地不顯示切換器
    }

    userSwitcher?.classList.toggle('hidden', !isTestMode);

    if (!isTestMode) {
        if (tierSwitcher) {
            tierSwitcher.classList.add('hidden');
        }
        return;
    }

    // 確認為測試模式後，才查當前 tier 並顯示切換器
    try {
        const data = await AppAPI.get('/api/test-mode/current-tier');
        if (data.is_test_mode) {
            if (tierSwitcher) {
                tierSwitcher.classList.remove('hidden');
                updateTierButtons(data.tier);
            }
        }
    } catch (err) {
        if (tierSwitcher) {
            tierSwitcher.classList.add('hidden');
        }
    }
}

/**
 * 切換測試帳號等級
 */
async function handleSwitchTestTier(tier) {
    const btn = document.querySelector(`.test-tier-btn[data-tier="${tier}"]`);
    if (!btn) return;

    // 顯示 loading 狀態
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i data-lucide="loader" class="w-3 h-3 animate-spin"></i>';
    btn.disabled = true;

    try {
        const data = await AppAPI.post('/api/test-mode/switch-tier', { tier: tier });

        if (data.success) {
            // 更新當前等級顯示
            document.getElementById('current-test-tier').textContent = tier.toUpperCase();

            // 更新按鈕狀態
            updateTierButtons(tier);

            // 重新載入工具設定以反映新等級的工具
            if (typeof initToolSettings === 'function') {
                await initToolSettings();
            }

            // 顯示成功訊息
            if (typeof showToast === 'function') {
                showToast(data.message, 'success', 3000);
            }
        } else {
            throw new Error(data.message || (window.I18n ? window.I18n.t('testMode.switchFailed') : 'Switch failed'));
        }
    } catch (err) {
        console.error('[testMode] Switch tier error:', err);

        if (typeof showToast === 'function') {
            showToast((window.I18n ? window.I18n.t('testMode.switchTierFailed') : 'Switch tier failed') + ': ' + (err.message || 'Unknown error'), 'error');
        }
    } finally {
        // 恢復按鈕狀態
        btn.innerHTML = originalText;
        btn.disabled = false;

        // 重新創建圖標
        if (window.AppUtils) window.AppUtils.refreshIcons();
    }
}

/**
 * 更新等級按鈕的選中狀態
 */
function updateTierButtons(currentTier) {
    document.querySelectorAll('.test-tier-btn').forEach(btn => {
        const tier = btn.dataset.tier;
        btn.classList.remove('bg-primary/20', 'text-primary', 'bg-accent/10', 'text-accent', 'bg-textMuted/10', 'ring-2', 'ring-primary/50');

        if (tier === currentTier) {
            // 選中狀態
            if (tier === 'premium') {
                btn.classList.add('bg-primary/20', 'text-primary', 'ring-2', 'ring-primary/50');
            } else {
                btn.classList.add('bg-textMuted/10', 'ring-2', 'ring-textMuted/50');
            }
        }
    });
}

// 導出函數供全局使用
window.handleSwitchTestTier = handleSwitchTestTier;
window.initTestMode = initTestMode;

export { initTestMode, handleSwitchTestTier, updateTierButtons };
