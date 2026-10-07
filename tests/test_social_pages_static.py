"""好友／私訊頁的靜態回歸測試（2026-09-26 論壇開放前盤查找到的三個頁面 bug）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_public_profile_avatar_does_not_reuse_auth_managed_id():
    """auth.js 會把「登入者自己」的頭像字母寫進 #profile-avatar；
    別人的個人頁若沿用這個 id，會顯示成訪客自己的頭像。"""
    auth_js = (WEB / "js" / "auth.js").read_text(encoding="utf-8")
    assert "'profile-avatar'" in auth_js  # 前提：auth.js 確實管這個 id
    profile_html = (WEB / "forum" / "profile.html").read_text(encoding="utf-8")
    assert 'id="profile-avatar"' not in profile_html
    assert 'id="public-profile-avatar"' in profile_html


def test_profile_page_initialises_i18n():
    """profile 頁不載 forum-app.js，沒人呼叫 I18n.init() 時整頁停在英文、toast 顯示原始 key"""
    js = (WEB / "forum" / "js" / "profile-page.js").read_text(encoding="utf-8")
    assert "I18n.init()" in js


def test_no_i18n_ternary_precedence_bug():
    """`a || window.I18n ? t(...) : '...'` 會被解析成 `(a || window.I18n) ? ...`，
    傳進來的 a 永遠被預設文字蓋掉"""
    offenders = []
    for path in WEB.rglob("*.js"):
        rel = path.relative_to(WEB).as_posix()
        if rel.startswith(("assets/", "node_modules/")) or "/node_modules/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(\|\||\+)\s*window\.I18n\s*\?", text):
            offenders.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}")
    assert offenders == []
