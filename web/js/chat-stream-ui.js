// ========================================
// chat-stream-ui.js - Shared chat streaming helpers
// 職責：共用 timer/progress/SSE buffer 邏輯
// ========================================

/**
 * 格式化秒數為易讀字串：< 60s 顯示 `45s`，≥ 60s 顯示 `1m20s`。
 * 不預測未來，只誠實呈現已花時間。
 */
function formatElapsed(seconds) {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return s > 0 ? `${m}m${s}s` : `${m}m`;
}

const ChatStreamUI = {
    updateTimers(targetDiv, elapsedSeconds) {
        if (!targetDiv) return;
        const formatted = typeof elapsedSeconds === 'number'
            ? formatElapsed(elapsedSeconds)
            : `${elapsedSeconds}s`;
        targetDiv.querySelectorAll('#loading-timer').forEach((display) => {
            display.textContent = formatted;
        });
    },

    /**
     * 更新當前分析階段標籤（顯示在計時器旁，如「正在查詢技術指標」）。
     * 不預測剩餘時間（業界共識：agent 任務時長不可預測，錯誤 ETA 比沒有更糟），
     * 只誠實呈現「已分析 Ns・正在做什麼」。
     */
    updateStage(targetDiv, stageLabel) {
        if (!targetDiv) return;
        targetDiv.querySelectorAll('.elapsed-stage').forEach((el) => {
            el.textContent = stageLabel || '';
        });
    },

    applyProgress(botMsgDiv, progressData) {
        if (!botMsgDiv || !progressData) return;

        if (progressData.message) {
            const loadingLabel = botMsgDiv.querySelector('.process-container span.font-medium');
            if (loadingLabel) {
                loadingLabel.textContent = progressData.message;
            }
        }

        // 非 agent 步驟事件（純文字切換）不需要步驟行
        if (progressData.type !== 'agent_start' && progressData.type !== 'agent_finish') {
            return;
        }

        const stepNum = progressData.step;
        let stepEl = botMsgDiv.querySelector(`.plan-step[data-step="${stepNum}"]`);

        // 找不到步驟行 → 動態建立。初始 UI 只有「Thinking...」頭，工具步驟是
        // 隨 agent_start 事件即時 append 的，讓使用者看到「第幾個工具・正在查詢什麼」。
        if (!stepEl && progressData.type === 'agent_start') {
            const container = botMsgDiv.querySelector('.process-container');
            if (!container) return;
            // 確保步驟區有容器（只建一次）
            let stepsBox = container.querySelector('.progress-steps');
            if (!stepsBox) {
                stepsBox = document.createElement('div');
                stepsBox.className = 'progress-steps px-4 pb-3 space-y-1';
                container.appendChild(stepsBox);
            }
            const label = progressData.task_name || progressData.task_id || '';
            stepEl = document.createElement('div');
            stepEl.className = 'plan-step flex items-center gap-2 text-xs text-textMuted rounded-md border border-borderSubtle/50 px-2 py-1 transition-colors';
            stepEl.dataset.step = stepNum;
            stepEl.innerHTML =
                `<span class="plan-check w-3 h-3 flex items-center justify-center text-textMuted/50"></span>` +
                `<span class="plan-label">${label}</span>`;
            stepsBox.appendChild(stepEl);
        }

        if (!stepEl) return;

        const check = stepEl.querySelector('.plan-check');
        if (progressData.type === 'agent_start') {
            if (check) {
                check.innerHTML =
                    '<i data-lucide="loader-2" class="w-3 h-3 text-primary animate-spin"></i>';
            }
            stepEl.classList.add('bg-primary/5', 'border-primary/20');
            // 階段標籤結合計時器：誠實呈現「正在查詢 X」，不預測剩餘時間
            this.updateStage(botMsgDiv, progressData.task_name || progressData.task_id || '');
        } else if (progressData.type === 'agent_finish') {
            if (check) {
                if (progressData.success) {
                    check.innerHTML = '<i data-lucide="check" class="w-3 h-3 text-primary"></i>';
                } else {
                    check.innerHTML =
                        '<i data-lucide="alert-circle" class="w-3 h-3 text-danger"></i>';
                    stepEl.classList.add('border-danger/20');
                }
            }
            stepEl.classList.remove('bg-primary/5', 'animate-pulse');
            // 工具完成後清空階段標籤（等下一個工具或合成回覆）
            this.updateStage(botMsgDiv, '');
        }

        if (window.lucide && typeof window.createIconsIn === 'function') {
            window.createIconsIn(botMsgDiv);
        }
    },

    consumeChunk(buffer, chunk) {
        const pending = (buffer || '') + chunk;
        const lines = pending.split('\n');
        return {
            lines: lines.slice(0, -1),
            pending: lines[lines.length - 1] || '',
        };
    },
};
window.ChatStreamUI = ChatStreamUI;

export { ChatStreamUI };
