"""前端打的 API 端點，後端必須真的註冊（2026-09-05）。

實際踩到：``web/js/forum-app.js`` 每一則留言都渲染檢舉鈕 → 開 modal →
POST /api/governance/reports。但後端的 governance router 自 2026-08-13 起
被 api_server.py 的 SAFETY_FEATURE_ENABLED=False 關閉，那支端點一律 404。

**後端刻意關掉功能、前端入口沒跟著撤**——按鈕壞了三週沒有任何測試、監控
或使用者回報抓到。這一類（前後端契約單邊改動）沒有守衛就只能靠人記得。

比對來源是 app.openapi()["paths"]——那是真正註冊的路由表。用 grep 掃
``@router.xxx`` 會漏掉 APIRouter(prefix=...) 而給出一堆假警報（本檔第一版
就是這樣量出「111/112 打不到」的荒謬數字）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]

# 後端刻意關閉、前端入口已撤，但程式碼還留著的端點。
# 列在這裡是為了讓守衛擋下**新的**斷線，同時不把既有決策掃到地毯下——
# 功能一旦重新啟用就要從這裡移掉（下面的 honest 測試會盯著）。
# （2026-09-26：/api/governance/reports 已單獨恢復——論壇檢舉；governance 其餘端點仍關，
#  但前端沒有入口，所以不用列。）
KNOWN_DISABLED: set = set()


def _registered_paths() -> set:
    import sys

    sys.path.insert(0, str(ROOT))
    from api_server import app

    return {re.sub(r"\{[^}]+\}", "{}", p).rstrip("/") for p in app.openapi()["paths"]}


def _frontend_refs() -> dict:
    """{端點: 檔案:行} ——只收非註解行的字面字串。"""
    refs = {}
    # 整個 web/（不只 web/js）：2026-09-26 文章頁的檢舉在 web/forum/js/post-page.js
    # 打了已關閉的端點，只掃 web/js 的時候這裡看不到
    for f in (ROOT / "web").rglob("*.js"):
        if "node_modules" in str(f) or "/i18n/" in str(f) or "/assets/" in str(f):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue  # 註解裡的範例不算（/api/security/csp-report 就是）
            for m in re.findall(r"""['"`](/api/[a-zA-Z0-9_\-/]+)['"`]""", line):
                refs.setdefault(m.rstrip("/") or m, f"{f.relative_to(ROOT)}:{i}")
    return refs


def _is_satisfied(ref: str, registered: set) -> bool:
    """字面值命中，或是某條註冊路徑的**前綴**——後者代表前端在字串拼接
    （'/api/wallet-monitor/wallet/' + address + '/detail'）。不認前綴的話
    會量出一堆假警報。"""
    norm = re.sub(r"\{[^}]+\}", "{}", ref).rstrip("/")
    if norm in registered:
        return True
    return any(r.startswith(norm + "/") for r in registered)


def test_every_frontend_endpoint_is_registered():
    registered = _registered_paths()
    dangling = {
        p: where for p, where in _frontend_refs().items()
        if p not in KNOWN_DISABLED and not _is_satisfied(p, registered)
    }
    assert not dangling, (
        "前端打的端點後端沒有註冊——使用者按下去必然 404：\n"
        + "\n".join(f"  {p}  ({w})" for p, w in sorted(dangling.items()))
        + "\n（若後端是刻意關閉某功能，前端入口要一起撤，不要留保證失敗的按鈕）"
    )


def test_known_disabled_list_stays_honest():
    """功能恢復了就要從清單移掉，否則清單本身變成掩蓋用的地毯。"""
    registered = _registered_paths()
    revived = [p for p in KNOWN_DISABLED if _is_satisfied(p, registered)]
    assert not revived, f"這些端點已經註冊回來了，請從 KNOWN_DISABLED 移除：{revived}"


def test_forum_report_is_the_only_governance_endpoint_open():
    """論壇檢舉恢復（2026-09-26）只開 POST /api/governance/reports；社群投票、
    違規紀錄、聲望等 governance 端點照 2026-08-13 決策維持關閉。"""
    import sys

    sys.path.insert(0, str(ROOT))
    from api_server import SAFETY_FEATURE_ENABLED, app

    governance = sorted(
        (tuple(sorted(r.methods)), r.path)
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/governance")
    )
    if SAFETY_FEATURE_ENABLED:
        pytest.skip("整個 governance router 已開")
    assert governance == [(("POST",), "/api/governance/reports")], governance


def test_comment_report_button_is_rendered():
    src = (ROOT / "web/js/forum-app.js").read_text(encoding="utf-8")
    i = src.index('data-report-type="comment"')
    tag = src[src.rindex("<button", 0, i) : src.index(">", i)]
    assert "hidden" not in tag, tag


# ---------------------------------------------------------------------------
# 冒煙檢查與 nav-config 的一致性（2026-09-05）
# ---------------------------------------------------------------------------
# smoke_postdeploy.py 的「訪客點鎖定分頁該彈登入窗」用 'crypto'，但
# 2026-08-27 的「訪客模式 Tier 1」把 crypto 開放給訪客了——檢查在守一個
# 一週後就被改掉的行為。而 smoke.yml 自己有語法錯從沒執行過，所以這個
# 過期躺了兩週沒人發現：**壞掉的檢查看不出自己壞掉**。


def _guest_allowed_tabs() -> set:
    src = (ROOT / "web/js/nav-config.js").read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(r"id: '([a-z-]+)'[^}]*?guestAllowed: true", src)
    }


def test_smoke_uses_a_tab_that_really_requires_login():
    smoke = (ROOT / "scripts/smoke_postdeploy.py").read_text(encoding="utf-8")
    m = re.search(r"switchTab\('([a-z-]+)'\)", smoke)
    assert m, "找不到冒煙檢查用的分頁"
    tab = m.group(1)
    allowed = _guest_allowed_tabs()
    assert tab not in allowed, (
        f"冒煙檢查用 '{tab}' 當「鎖定分頁」，但 nav-config 標了 guestAllowed: true"
        f"——訪客本來就進得去，不會彈登入窗。目前開放訪客的有：{sorted(allowed)}"
    )
    assert tab != "chat", "chat 對訪客永遠開放（訪客模式的入口）"
