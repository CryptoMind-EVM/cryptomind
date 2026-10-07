"""固定 chrome 的容器不可建立 fixed containing block。

底部的固定元素（聊天輸入框、無金鑰提示條）住在 .tab-content 裡，靠
`position: fixed` + `bottom: <距視窗底部>` 定位。只要祖先出現 transform、
perspective、filter 或 will-change: transform，那個祖先就會變成 fixed 子孫的
containing block，bottom 改成相對祖先計算 —— 底部整組會位移並隨動畫漂動。

實際事故（#256）：.tab-content 有 `animation: slideUpFade forwards` 與
`will-change: transform`，兩者各自都會建立參考框，而且 forwards 結束後留下的
translateY(0) 仍然是 transform，參考框永遠不會消失。結果輸入框壓在導覽列上，
提示條蓋住卡片按鈕。

opacity 不會建立 containing block，所以分頁轉場只做淡入。
"""

from __future__ import annotations

import re
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[1] / "web" / "styles.css"

# 這些 selector 是固定 chrome 的祖先容器
GUARDED_SELECTORS = (".tab-content", "#main-content", "body")

# 會讓元素成為 fixed 子孫 containing block 的宣告
TRANSFORM_DECL = re.compile(r"(?<![\w-])transform\s*:\s*(?!none\s*[;}])", re.I)
PERSPECTIVE_DECL = re.compile(r"(?<![\w-])perspective\s*:\s*(?!none\s*[;}])", re.I)
FILTER_DECL = re.compile(r"(?<![\w-])filter\s*:\s*(?!none\s*[;}])", re.I)
WILL_CHANGE_RISKY = re.compile(
    r"will-change\s*:[^;}]*\b(transform|perspective|filter)\b", re.I
)
# animation: <name> ... 取出動畫名稱
ANIMATION_NAME = re.compile(
    r"animation(?:-name)?\s*:\s*([a-zA-Z_][\w-]*)", re.I
)


def _rules(css: str):
    """回傳 [(selector, body)]，忽略註解與 @keyframes 內部。"""
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    without_keyframes = re.sub(
        r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", stripped, flags=re.S
    )
    return [
        (" ".join(m.group(1).split()), m.group(2))
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", without_keyframes, re.S)
    ]


def _keyframe_uses_transform(css: str, name: str) -> bool:
    match = re.search(
        rf"@keyframes\s+{re.escape(name)}\s*\{{(.*?)\n\}}", css, re.S
    )
    return bool(match and TRANSFORM_DECL.search(match.group(1)))


def test_fixed_chrome_ancestors_do_not_create_containing_block():
    css = CSS_PATH.read_text(encoding="utf-8")
    failures = []

    for selector, body in _rules(css):
        target = next(
            (g for g in GUARDED_SELECTORS if re.search(rf"(^|[\s,>]){re.escape(g)}(\s|,|$|:)", selector)),
            None,
        )
        if not target:
            continue

        if TRANSFORM_DECL.search(body):
            failures.append(f"{selector}: 直接宣告了 transform")
        if PERSPECTIVE_DECL.search(body):
            failures.append(f"{selector}: 直接宣告了 perspective")
        if FILTER_DECL.search(body):
            failures.append(f"{selector}: 直接宣告了 filter")
        if WILL_CHANGE_RISKY.search(body):
            failures.append(f"{selector}: will-change 列了會建立參考框的屬性")

        for anim in ANIMATION_NAME.findall(body):
            if anim in ("none", "inherit", "initial", "unset"):
                continue
            if _keyframe_uses_transform(css, anim):
                failures.append(
                    f"{selector}: 動畫 {anim} 的 keyframes 使用 transform"
                )

    assert not failures, (
        "以下規則會讓固定 chrome 的祖先變成 fixed containing block，"
        "底部元素會相對它而不是視窗定位：\n  " + "\n  ".join(failures)
    )
