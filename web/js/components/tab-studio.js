// Tab: Studio — 提案工作台（募資者模式，design 2026-08-16）
// 雙模式：出資者用 Discover；募資者用本工作台（編輯器＋AI 教練＋版本軌跡）。
// 樣式全走設計 token（border-borderSubtle/10、text-white on primary）——
// 深淺主題自動翻轉（PR #470 教訓）。
window.Components = window.Components || {};
window.Components['studio'] = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header -->
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="studio.title">提案工作台</h2>
                <div class="flex items-center gap-2">
                    <button data-click="StudioTab.refresh" class="${ICON_ACTION_BUTTON_CLASS}" aria-label="refresh" data-i18n="common.refresh" data-i18n-attr="aria-label">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="studio-tab-body" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <div class="max-w-4xl mx-auto pb-10">

                        <!-- ══ 列表視圖 ══ -->
                        <div id="studio-list-view">
                            <!-- 降級橫幅（flag off / 載入失敗） -->
                            <div id="studio-degraded" class="hidden mb-4 p-3 rounded-xl border border-destructive/40 bg-destructive/10 text-sm flex items-center gap-2">
                                <i data-lucide="cloud-off" class="w-4 h-4"></i>
                                <span id="studio-degraded-text" data-i18n="studio.disabled">功能未開放</span>
                            </div>

                            <!-- 新增草稿 -->
                            <div class="mb-6 p-4 rounded-xl border border-borderSubtle/10 bg-surface">
                                <div class="flex items-center gap-2 mb-3">
                                    <i data-lucide="plus-circle" class="w-4 h-4 text-primary"></i>
                                    <h3 class="text-sm font-medium text-secondary" data-i18n="studio.newDraft">新增草稿</h3>
                                </div>
                                <div class="flex flex-col sm:flex-row gap-2">
                                    <input id="studio-new-title" type="text" maxlength="120"
                                        class="flex-1 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                        data-i18n="studio.newTitlePlaceholder" data-i18n-attr="placeholder"
                                        data-i18n="studio.draftTitlePlaceholder" data-i18n-attr="placeholder" placeholder="草稿標題，例如：AI safety 影片系列">
                                    <input id="studio-new-cause" type="text" maxlength="100"
                                        class="sm:w-48 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                        data-i18n="studio.newCausePlaceholder" data-i18n-attr="placeholder"
                                        data-i18n="studio.causePlaceholder" data-i18n-attr="placeholder" placeholder="Cause（選填）">
                                    <button data-click="StudioTab.createDraft"
                                        class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium whitespace-nowrap"
                                        data-i18n="studio.create">建立</button>
                                </div>
                            </div>

                            <!-- 草稿列表 -->
                            <div id="studio-draft-list" class="grid grid-cols-1 gap-3"></div>

                            <p class="mt-6 text-xs text-textMuted text-center" data-i18n="studio.listNote">
                                草稿與版本軌跡僅自己可見；匯出後請本人到 Manifund 送出。
                            </p>
                        </div>

                        <!-- ══ 編輯視圖 ══ -->
                        <div id="studio-editor-view" class="hidden">
                            <!-- 編輯器表頭 -->
                            <div class="flex flex-wrap items-center gap-2 mb-4">
                                <button data-click="StudioTab.backToList"
                                    class="px-3 py-1.5 rounded-lg bg-surfaceHighlight text-xs font-medium inline-flex items-center gap-1.5">
                                    <i data-lucide="arrow-left" class="w-3.5 h-3.5"></i>
                                    <span data-i18n="studio.backToList">返回列表</span>
                                </button>
                                <span id="studio-editor-status" class="text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight"></span>
                                <span id="studio-editor-versions" class="text-xs text-textMuted"></span>
                                <div class="ml-auto flex gap-2">
                                    <button data-click="StudioTab.saveVersion"
                                        class="px-4 py-1.5 rounded-lg bg-primary text-background text-sm font-medium"
                                        data-i18n="studio.save">存檔（新版本）</button>
                                    <button data-click="StudioTab.exportDraft"
                                        class="px-4 py-1.5 rounded-lg bg-surfaceHighlight text-sm font-medium inline-flex items-center gap-1.5">
                                        <i data-lucide="file-output" class="w-3.5 h-3.5"></i>
                                        <span data-i18n="studio.export">匯出</span>
                                    </button>
                                    <button data-click="StudioTab.shareDraft"
                                        class="px-4 py-1.5 rounded-lg bg-surfaceHighlight text-sm font-medium inline-flex items-center gap-1.5">
                                        <i data-lucide="share-2" class="w-3.5 h-3.5"></i>
                                        <span data-i18n="studio.share">分享軌跡</span>
                                    </button>
                                    <button data-click="StudioTab.deleteDraft"
                                        class="px-3 py-1.5 rounded-lg bg-destructive/10 text-destructive text-sm"
                                        data-i18n="studio.delete">刪除</button>
                                </div>
                            </div>

                            <!-- 草稿中繼資料 -->
                            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
                                <div>
                                    <label class="block text-xs text-textMuted mb-1" data-i18n="studio.titleLabel">標題</label>
                                    <input id="studio-edit-title" type="text" maxlength="120"
                                        class="w-full px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm">
                                </div>
                                <div>
                                    <label class="block text-xs text-textMuted mb-1" data-i18n="studio.causeLabel">Cause（教練語境用）</label>
                                    <input id="studio-edit-cause" type="text" maxlength="100"
                                        class="w-full px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm">
                                </div>
                            </div>

                            <!-- 編輯器＋預覽 -->
                            <div class="rounded-xl border border-borderSubtle/10 bg-surface p-3 mb-4">
                                <div class="flex items-center justify-between mb-2">
                                    <span class="text-xs text-textMuted" id="studio-dirty-hint"></span>
                                    <button data-click="StudioTab.togglePreview"
                                        class="text-xs px-2 py-1 rounded-lg bg-surfaceHighlight text-textMuted hover:text-secondary"
                                        data-i18n="studio.preview">預覽</button>
                                </div>
                                <textarea id="studio-editor" rows="14"
                                    class="w-full md:min-h-[60dvh] bg-background/50 border border-borderSubtle/10 rounded-lg px-3 py-2 text-sm text-secondary font-mono resize-y focus:outline-none focus:border-primary/40"
                                    placeholder="Markdown…"></textarea>
                                <pre id="studio-preview" class="hidden w-full md:min-h-[60dvh] bg-background/50 border border-borderSubtle/10 rounded-lg px-3 py-2 text-sm text-secondary whitespace-pre-wrap"></pre>
                            </div>

                            <!-- AI 教練 -->
                            <!-- 參考專案（Discover→Studio 動線，design 2026-08-17 §Phase 2） -->
                            <div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 mb-4">
                                <div class="flex items-center gap-2 mb-2">
                                    <i data-lucide="bookmark" class="w-4 h-4 text-danger"></i>
                                    <h3 class="text-sm font-medium text-secondary" data-i18n="studio.referencesTitle">參考專案</h3>
                                </div>
                                <div id="studio-references" class="min-h-[1.5rem]"></div>
                            </div>

                            <div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 mb-4">
                                <div class="flex items-center gap-2 mb-1">
                                    <i data-lucide="graduation-cap" class="w-4 h-4 text-primary"></i>
                                    <h3 class="text-sm font-medium text-secondary" data-i18n="studio.coach.title">AI 教練</h3>
                                </div>
                                <p class="text-xs text-textMuted mb-3" data-i18n="studio.coach.note">
                                    教練看你的稿＋同領域已獲資助專案，只提問與建議——稿件永遠是你寫的。
                                </p>
                                <label class="flex items-center gap-2 mb-3 text-xs text-textMuted cursor-pointer">
                                    <input id="studio-coach-refs" type="checkbox" class="accent-primary">
                                    <span data-i18n="studio.coach.useRefs">讓教練同時參考我加入的參考專案</span>
                                </label>
                                <div class="flex gap-2 mb-3">
                                    <input id="studio-coach-question" type="text" maxlength="500"
                                        class="flex-1 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                        data-i18n="studio.coach.questionPlaceholder" data-i18n-attr="placeholder"
                                        data-i18n="studio.coach.questionPlaceholder" data-i18n-attr="placeholder" placeholder="想問教練什麼？（選填，例如：金額怎麼寫才有說服力）">
                                    <button data-click="StudioTab.askCoach"
                                        class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium whitespace-nowrap"
                                        data-i18n="studio.coach.ask">請教練看稿</button>
                                </div>
                                <div id="studio-coach-result" class="space-y-4"></div>
                            </div>

                            <!-- 版本軌跡 -->
                            <div class="rounded-xl border border-borderSubtle/10 bg-surface p-4">
                                <div class="flex items-center gap-2 mb-3">
                                    <i data-lucide="history" class="w-4 h-4 text-primary"></i>
                                    <h3 class="text-sm font-medium text-secondary" data-i18n="studio.trail.title">版本軌跡</h3>
                                </div>
                                <div id="studio-trail-curve" class="mb-3"></div>
                                <div id="studio-trail" class="space-y-2"></div>
                                <div id="studio-diff-box" class="hidden mt-3"></div>
                            </div>
                        </div>

                    </div>
                </div>
            </div>
        </div>

        <!-- 分享軌跡 modal（平台樣式） -->
        <div id="studio-share-modal" class="hidden fixed inset-0 z-[70] bg-background/80 backdrop-blur-sm p-4 overflow-y-auto" data-click="StudioTab.closeShare">
            <div class="max-w-lg mx-auto my-8 rounded-2xl border border-borderSubtle/10 bg-surface shadow-xl" role="dialog" aria-modal="true" data-click="StudioTab._noop">
                <div class="flex items-start justify-between gap-3 p-5 border-b border-borderSubtle/10">
                    <div>
                        <h3 class="font-serif text-xl text-secondary" data-i18n="studio.shareTitle">分享版本軌跡</h3>
                        <p class="text-xs text-textMuted mt-1" data-i18n="studio.shareNote">唯讀連結，任何人可看出版演化史；隨時可撤銷。</p>
                    </div>
                    <button data-click="StudioTab.closeShare" class="p-2 -mr-2 -mt-1 rounded-full hover:bg-surfaceHighlight text-textMuted" aria-label="close" data-i18n="common.close" data-i18n-attr="aria-label">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="p-5 space-y-3">
                    <input id="studio-share-url" type="text" readonly
                        class="w-full bg-background/50 border border-borderSubtle/10 rounded-lg px-3 py-2 text-xs text-secondary font-mono">
                    <div class="flex gap-2">
                        <button data-click="StudioTab.copyShare"
                            class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium whitespace-nowrap" data-i18n="studio.copyLink">複製連結</button>
                        <button data-click="StudioTab.revokeShare"
                            class="px-4 py-2 rounded-lg bg-destructive/10 text-destructive text-sm whitespace-nowrap" data-i18n="studio.revoke">撤銷分享</button>
                    </div>
                    <p class="text-xs text-textMuted" data-i18n="studio.sharePrivacy">連結內容不含你的帳號資訊，只含稿件與版本軌跡。</p>
                </div>
            </div>
        </div>

        <!-- 手機浮動「請教練看稿」（教練區進入視野時自動隱藏） -->
        <button id="studio-coach-fab" data-click="StudioTab.scrollToCoach"
            class="hidden fixed bottom-28 right-4 z-40 md:!hidden px-4 py-2.5 rounded-full bg-primary text-background text-sm font-medium shadow-lg items-center gap-2 whitespace-nowrap">
            <i data-lucide="graduation-cap" class="w-4 h-4"></i>
            <span data-i18n="studio.coach.floatBtn">請教練看稿</span>
        </button>

        <!-- 匯出 modal（平台樣式） -->
        <div id="studio-export-modal" class="hidden fixed inset-0 z-[70] bg-background/80 backdrop-blur-sm p-4 overflow-y-auto" data-click="StudioTab.closeExport">
            <div class="max-w-2xl mx-auto my-8 rounded-2xl border border-borderSubtle/10 bg-surface shadow-xl" role="dialog" aria-modal="true" data-click="StudioTab._noop">
                <div class="flex items-start justify-between gap-3 p-5 border-b border-borderSubtle/10">
                    <h3 class="font-serif text-xl text-secondary" data-i18n="studio.exportTitle">匯出草稿</h3>
                    <button data-click="StudioTab.closeExport" class="p-2 -mr-2 -mt-1 rounded-full hover:bg-surfaceHighlight text-textMuted" aria-label="close" data-i18n="common.close" data-i18n-attr="aria-label">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="p-5 space-y-3">
                    <p class="text-sm text-textMuted" data-i18n="studio.exportNote">
                        複製以下 markdown，到 Manifund 貼上送出——送出動作永遠由你本人完成。
                    </p>
                    <textarea id="studio-export-content" rows="12" readonly
                        class="w-full bg-background/50 border border-borderSubtle/10 rounded-lg px-3 py-2 text-xs text-secondary font-mono resize-y"></textarea>
                    <div class="flex gap-2">
                        <button data-click="StudioTab.copyExport"
                            class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium whitespace-nowrap"
                            data-i18n="studio.copy">複製</button>
                        <a href="https://manifund.org/projects/new" target="_blank" rel="noopener noreferrer"
                            class="px-4 py-2 rounded-lg bg-surfaceHighlight text-sm inline-flex items-center gap-2 whitespace-nowrap">
                            <span data-i18n="studio.goManifund">前往 Manifund</span>
                            <i data-lucide="external-link" class="w-3 h-3"></i>
                        </a>
                    </div>
                </div>
            </div>
        </div>
`;
