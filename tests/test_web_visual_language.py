"""Regression tests for CryptoMind's restrained market-workspace visual language."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reference_inspired_palette_is_synced_across_design_sources():
    tailwind = (ROOT / "tailwind.config.js").read_text(encoding="utf-8")
    styles = (ROOT / "web/styles.css").read_text(encoding="utf-8")
    design_system = (ROOT / "docs/DESIGN_SYSTEM.md").read_text(encoding="utf-8")

    # token 已改成 CSS-var-backed（色頻通道格式），讓 /<alpha> 透明度修飾詞生效。
    # tailwind.config.js 的每個顏色 token 都要用 rgb(var(--color-xxx) / <alpha-value>) 格式。
    css_var_tokens = [
        "background",
        "surface",
        "surfaceHighlight",
        "primary",
        "secondary",
        "accent",
        "textMain",
        "textMuted",
    ]
    for token in css_var_tokens:
        assert f"{token}: 'rgb(var(--color-" in tailwind, f"{token} 未使用 CSS var 格式"

    # styles.css 必須有深淺雙 theme：:root（預設淺）+ html.dark（深色覆寫）
    assert ":root" in styles
    assert "html.dark" in styles
    # 淺色色頻：亮灰底 #F5F5F7（非純白，降低刺眼）+ 加深墨字 #17171C（WCAG 對比）
    assert "--color-background: 245 245 247" in styles  # #F5F5F7
    assert "--color-text-primary: 23 23 28" in styles  # #17171C
    # 深色覆寫（既有墨色系）
    assert "--color-background: 20 22 31" in styles  # #14161F dark
    # color-scheme 雙值
    assert "color-scheme: light" in styles
    assert "color-scheme: dark" in styles

    # DESIGN_SYSTEM.md 仍記載 top.co 靈感來源與 token 表
    assert "#F5F5F7" in design_system
    assert "#14161F" in design_system


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG 相對亮度對比公式。"""
    def channel(hex_part: str) -> float:
        c = int(hex_part, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def lum(h: str) -> float:
        h = h.lstrip("#")
        return 0.2126 * channel(h[0:2]) + 0.7152 * channel(h[2:4]) + 0.0722 * channel(h[4:6])

    l1, l2 = lum(fg_hex), lum(bg_hex)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def test_light_theme_text_contrast_meets_wcag_aa():
    """淺色 theme 的文字對比必須達 WCAG AA（小字 ≥4.5）。

    回歸測試：過去曾因 token 值選擇導致次要文字/藍底白字看不清。
    改任何淺色 token 值前，先確認這裡的對比組合仍及格。
    """
    # (前景, 背景, 最小對比)
    cases = [
        ("#17171C", "#F5F5F7", 4.5),   # 主文字 on 頁面底
        ("#37373C", "#FFFFFF", 4.5),   # 次文字 on 卡片白
        ("#4A4C54", "#F5F5F7", 4.5),   # muted on 頁面底（加深後 AAA）
        ("#4A4C54", "#EEEEF0", 4.5),   # muted on surfaceHighlight
        ("#FFFFFF", "#2563EB", 4.5),   # 白字 on primary 藍（加深後過 AA）
        ("#2563EB", "#FFFFFF", 4.5),   # primary 藍字 on 卡片白（連結）
    ]
    for fg, bg, minimum in cases:
        ratio = _contrast_ratio(fg, bg)
        assert ratio >= minimum, f"{fg} on {bg}: {ratio:.2f} < {minimum} (WCAG AA)"


def test_primary_shell_avoids_glowing_ai_dashboard_cliches():
    """2026-08-24 DANNY 決策修訂：品牌 logo 回歸（側欄品牌列 28px＋歡迎畫面
    80×80 含漸層光暈，PR #553）——8/3 設計系統「純文字品牌」政策撤銷。
    其餘反 cliché 守門（hero glow、導覷浮誇樣式）照舊。"""
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    welcome = (ROOT / "web/js/chat-sessions.js").read_text(encoding="utf-8")
    nav = (ROOT / "web/js/global-nav.js").read_text(encoding="utf-8")

    assert "shell-hero-glow" not in index
    # 品牌 logo 必須在（2026-08-24 DANNY 指示恢復，防再次無預警移除）
    assert 'src="/static/img/title_icon.png" alt="CryptoMind Logo"' in index

    assert "market-workspace" in welcome
    assert "brand-mark" in welcome
    assert "welcome-actions" in welcome
    # 歡迎畫面 logo（80×80＋光暈）必須在（同上）
    assert "/static/img/title_icon.png" in welcome
    assert "linear-gradient(135deg, #0098EA" in welcome

    # 導覽只剩側欄功能選單（2026-09-24 手機底部列／論壇 pill 拔除）
    assert "getNavTemplate" not in nav
    menu = nav[nav.index("renderSidebarNav() {") : nav.index("toggleSidebarNavMore()")]
    assert "backdrop-blur-xl" not in menu
    assert "shadow-2xl" not in menu
    assert "rounded-full" not in menu

