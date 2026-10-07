"""click-delegator 用裸名稱呼叫的函式都要真的是全域（2026-09-29）。

click-delegator.js 是 classic script，`if (typeof X === 'function') X()` 查的是
全域。ES module 裡的函式只 export 不掛 window，typeof 永遠 'undefined'，動作被
默默跳過——沒有錯誤、沒有請求。

事故：91b72fe（2026-02）刪掉 friends.js 的 window.handleFriendSearch，好友搜尋
從此輸入什麼都不會打 /api/friends/search，使用者只看到「搜不到人」。同一次
掃出 auth.js 的 handleDevSwitchUser（TEST_MODE 切換帳號鈕）也是只 export。

「是全域」＝ 某支 JS 有 `window.X =`，或 X 是沒有 export 的 classic script 裡的
頂層 function 宣告（例如 forum/js/profile-page.js）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
CLICK_DELEGATOR = WEB / "js" / "click-delegator.js"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", src)


def _web_js_sources() -> dict[Path, str]:
    return {
        p: _strip_comments(p.read_text(encoding="utf-8"))
        for p in WEB.rglob("*.js")
        if not {"dist", "node_modules", "vendor"} & set(p.parts)
    }


def _guarded_names() -> set[str]:
    code = _strip_comments(CLICK_DELEGATOR.read_text(encoding="utf-8"))
    return set(
        re.findall(
            r"typeof\s+([A-Za-z_$][\w$]*)\s*===?\s*'function'\s*\)\s*\1\s*\(", code
        )
    )


def _is_global(name: str, sources: dict[Path, str]) -> bool:
    assign = re.compile(rf"\bwindow\.{re.escape(name)}\s*=(?!=)")
    top_level_fn = re.compile(rf"(?m)^(?:async\s+)?function\s+{re.escape(name)}\s*\(")
    is_module = re.compile(r"(?m)^\s*export\s")
    for src in sources.values():
        if assign.search(src):
            return True
        if top_level_fn.search(src) and not is_module.search(src):
            return True
    return False


def test_guard_regex_sees_the_calls():
    names = _guarded_names()
    # 錨點：正則要真的抓得到 delegator 的呼叫，不然下面的斷言恆真
    assert {"handleFriendSearch", "handleProfileBack", "handleAddFriend"} <= names


def test_typeof_guarded_names_are_reachable_globals():
    sources = _web_js_sources()
    unwired = sorted(n for n in _guarded_names() if not _is_global(n, sources))
    assert not unwired, (
        f"click-delegator 用 typeof 呼叫但不是全域（動作會被默默跳過）：{unwired}；"
        "在定義的 module 補 window.X = X"
    )
