"""claw_loop 重跑（nudge／clarify）的 SubTask 要跟主路徑一樣帶時間錨點。

主路徑 description 開頭有 `[REF: 現在 …]`；clarify 重跑時組的是新的
description（「使用者補充釐清＋原問題」），以前沒補錨點——釐清一輪之後
LLM 又退回訓練資料的年份。5 個重跑點改走同一個 _rerun_subtask。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.agents.manager import claw_loop
from core.agents.manager.claw_loop import (
    _TIME_ANCHOR_PREFIX,
    _prepend_current_time_anchor,
    _rerun_subtask,
)
from core.agents.models import SubTask

SOURCE = Path(claw_loop.__file__).read_text(encoding="utf-8")


def _base_task() -> SubTask:
    return SubTask(
        step=0,
        description=_prepend_current_time_anchor("原問題"),
        agent="cryptomind",
        context={"language": "zh-TW", "history": "h"},
    )


def test_rerun_subtask_prepends_time_anchor():
    task = _base_task()
    rerun = _rerun_subtask(task, "加密貨幣\n\n（使用者補充釐清）原問題：SNXX")
    assert rerun.description.startswith(_TIME_ANCHOR_PREFIX)
    assert rerun.description.endswith("（使用者補充釐清）原問題：SNXX")
    assert rerun.step == task.step + 1
    assert rerun.agent == task.agent
    assert rerun.context is task.context


def test_rerun_subtask_does_not_double_anchor():
    task = _base_task()
    already = _prepend_current_time_anchor("q")
    rerun = _rerun_subtask(task, already)
    assert rerun.description == already
    assert rerun.description.count(_TIME_ANCHOR_PREFIX) == 1


def test_all_rerun_sites_use_the_helper():
    """接線守衛：重跑點不可再手組 `SubTask(step=task.step + 1, …)`。

    手組的版本就是漏掉錨點的那幾處；只有 helper 本身可以出現這個寫法。
    """
    hand_built = re.findall(r"SubTask\(\s*step=task\.step \+ 1", SOURCE)
    assert len(hand_built) == 1, (
        f"發現 {len(hand_built) - 1} 處手組的重跑 SubTask——請改用 _rerun_subtask"
    )
    # nudge ×2（空回覆、數字驗證）＋ clarify ×3（模糊、答非所問、模型呼叫 clarify）
    assert len(re.findall(r"= _rerun_subtask\(", SOURCE)) == 5
