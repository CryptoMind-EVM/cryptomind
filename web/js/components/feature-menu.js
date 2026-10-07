// Navigation customization modal (lazy-loaded)
window.Components = window.Components || {};

// Feature Menu: Navigation Customization
window.Components.featureMenu = `
    <div id="feature-menu-modal" class="fixed inset-0 z-50 hidden">
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" data-click="FeatureMenu.close"></div>

        <!-- Modal Container -->
        <div class="${FEATURE_MODAL_STAGE_CLASS}">
            <div class="${FEATURE_MODAL_PANEL_CLASS}">

                <!-- Header -->
                <div class="p-6 border-b border-white/5">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-3">
                            <div class="${HERO_ICON_BOX_CLASS}">
                                <i data-lucide="layout-grid" class="w-5 h-5 text-primary"></i>
                            </div>
                            <div>
                                <h2 class="text-xl font-serif text-secondary" data-i18n="featureMenu.title">Customize Navigation</h2>
                                <p class="text-xs text-textMuted" data-i18n="featureMenu.description">Choose which features appear in your bottom navigation</p>
                            </div>
                        </div>
                        <button data-click="FeatureMenu.close" class="${ICON_ACTION_BUTTON_CLASS}">
                            <i data-lucide="x" class="w-5 h-5"></i>
                        </button>
                    </div>
                </div>

                <!-- Warning Banner -->
                <div id="feature-menu-warning" class="${WARNING_BANNER_CLASS}">
                    <div class="${WARNING_BANNER_ROW_CLASS}">
                        <i data-lucide="alert-triangle" class="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5"></i>
                        <p class="text-xs text-amber-600"></p>
                    </div>
                </div>

                <!-- Items Grid -->
                <div ${SHELL_MODAL_SCROLL_ATTR} class="flex-1 overflow-y-auto p-6">
                    <div id="feature-menu-items" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <!-- Items will be dynamically inserted here -->
                    </div>
                </div>

                <!-- Footer -->
                <div class="p-6 border-t border-white/5 space-y-3">
                    <div class="flex items-center gap-3">
                        <button data-click="FeatureMenu.save" class="flex-1 py-3 bg-primary hover:brightness-110 text-background font-bold rounded-xl transition flex items-center justify-center gap-2 shadow-lg shadow-primary/20">
                            <i data-lucide="check" class="w-4 h-4"></i>
                            <span data-i18n="featureMenu.saveChanges">Save Changes</span>
                        </button>
                        <button data-click="FeatureMenu.resetToDefaults" class="px-4 py-3 bg-white/5 hover:bg-white/10 text-textMuted rounded-xl transition flex items-center justify-center gap-2 border border-white/5">
                            <i data-lucide="rotate-ccw" class="w-4 h-4"></i>
                            <span data-i18n="featureMenu.reset">Reset</span>
                        </button>
                    </div>
                    <button data-click="FeatureMenu.close" class="w-full py-2.5 text-textMuted hover:text-textMain text-sm font-bold transition" data-i18n="common.cancel">
                        Cancel
                    </button>
                </div>
            </div>
        </div>
    </div>
    `;

// Side-effect module — assigns to window.Components
export {};
