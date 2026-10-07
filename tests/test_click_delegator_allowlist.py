"""click-delegator 白名單回歸測試（2026-08-25）。

背景：multi_consent 卡片按鈕（multiConsentToggle / multiConsentAll /
multiConsentSubmit）上線後「點了沒反應」——卡片渲染正常，但
click-delegator 是嚴格白名單制（防注入），未列名的 action 會靜默穿過
所有分派分支。本測試掃 HITL 卡片實際使用的每個 ``data-click`` action，
確保委派器認得——同類「按鈕dead」問題再也不會無聲上線。
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_WEB = Path(__file__).resolve().parent.parent / "web" / "js"

# 這些 action 由委派器的專屬 if 分支處理（非白名單）——掃描時同等視為可分發
_SPECIAL_CASED = {
    "switchTab",
    "showLegalPage",
    "changeChartInterval",
    "closeModal",
    "goHome",
}


def _delegator_source() -> str:
    return (_WEB / "click-delegator.js").read_text(encoding="utf-8")


def _hitl_actions() -> set:
    """HITL 卡片（chat-hitl.js / chat-analysis.js）實際使用的 data-click actions。"""
    actions = set()
    for fname in ("chat-hitl.js", "chat-analysis.js"):
        src = (_WEB / fname).read_text(encoding="utf-8")
        actions.update(re.findall(r'data-click="([A-Za-z][\w.]*)"', src))
    return actions


@pytest.mark.parametrize(
    "action", sorted(_hitl_actions()), ids=lambda a: f"action={a}"
)
def test_hitl_card_actions_are_dispatchable(action):
    src = _delegator_source()
    if action in _SPECIAL_CASED:
        pytest.skip(f"{action} 由委派器專屬分支處理")
    # 白名單（字串字面量）或其他明確分派點必須提到這個 action
    assert f"'{action}'" in src or f'"{action}"' in src, (
        f"data-click=\"{action}\" 不在 click-delegator 的分發面內——"
        "按鈕會渲染但點擊無反應（白名單制）。請加入 allowedActions 或專屬分支。"
    )


def test_multi_consent_actions_whitelisted():
    """2026-08-25 線上事故回歸：三個 multi_consent 按鈕 action 必須在白名單。"""
    src = _delegator_source()
    for action in ("multiConsentToggle", "multiConsentAll", "multiConsentSubmit"):
        assert f"'{action}'" in src, f"{action} 必須列在 allowedActions"


def _allowed_roots(src: str) -> set:
    block = src[src.index("var allowedRoots = new Set([") :]
    block = block[: block.index("]);")]
    return set(re.findall(r"'([A-Za-z_$][\w$]*)'", block))


def _dotted_click_roots() -> dict:
    """前端實際用到的 data-click="物件.方法" → {物件: 出現的檔案}。"""
    roots: dict = {}
    files = list(_WEB.rglob("*.js")) + list(_WEB.parent.glob("*.html"))
    for path in files:
        src = path.read_text(encoding="utf-8")
        for root in re.findall(r'data-click="([A-Za-z_$][\w$]*)\.[A-Za-z_$][\w$.]*"', src):
            roots.setdefault(root, path.name)
    return roots


def test_dotted_object_roots_are_allowed():
    """2026-09-25：TrustTab.refresh、AdminAuditManager/AdminVisitorsManager.changeRange
    （7/30/90 天）點了沒反應——物件不在 allowedRoots，委派器直接略過。"""
    roots = _dotted_click_roots()
    assert {"TrustTab", "AdminAuditManager", "AdminVisitorsManager"} <= set(roots), (
        "掃描沒抓到已知的物件型 action——正則或掃描範圍壞了，這個測試會變成空轉"
    )
    allowed = _allowed_roots(_delegator_source())
    missing = {r: f for r, f in roots.items() if r not in allowed}
    assert not missing, f"data-click 用了不在 allowedRoots 的物件（按鈕會沒反應）：{missing}"
