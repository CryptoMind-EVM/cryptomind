"""公開個人頁不能用 auth.js 會寫「登入者自己」資料的 id（2026-09-29）。

auth.js 的 _updateUI 會把登入者的名字／頭像／UID 寫進固定 id。個人頁
（forum/profile.html）看的是「別人」：以前名字用 #profile-username，打開好友個人頁
先閃自己的名字（DANNY 回報先看到 Danny 才變好友名稱），token 續登後還可能蓋回來。
頭像早就為同一件事改成 #public-profile-avatar，名字漏了。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WEB = Path(__file__).resolve().parents[1] / "web"


def _ids_auth_writes() -> set[str]:
    src = (WEB / "js" / "auth.js").read_text(encoding="utf-8")
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", src))
    # 批次寫名字的清單：['sidebar-user-name', ..., 'nav-username'].forEach(... applyUserNameField ...)
    for block in re.findall(r"\[([^\]]*)\]\.forEach\(\s*\(id\)", src):
        ids.update(re.findall(r"'([\w-]+)'", block))
    return ids


def test_regex_sees_auth_targets():
    ids = _ids_auth_writes()
    assert {"profile-username", "sidebar-user-name", "profile-uid"} <= ids


def test_public_profile_page_avoids_self_identity_ids():
    html = (WEB / "forum" / "profile.html").read_text(encoding="utf-8")
    page_ids = set(re.findall(r'\bid="([\w-]+)"', html))
    clash = sorted(page_ids & _ids_auth_writes())
    assert not clash, (
        f"個人頁用到 auth.js 會寫登入者自己資料的 id：{clash}——看好友個人頁會閃自己的資料"
    )
