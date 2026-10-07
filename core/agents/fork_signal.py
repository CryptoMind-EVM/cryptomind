"""loop_fork 的控制流訊號（Model Mixer Step 6，§6.3）。

單獨一個模組是為了避開 import 循環：訊號在 ``base_react_agent`` 的串流
回呼裡被丟出來，卻要由 ``manager/claw_loop`` 接住——後者 import 前者，
所以共用型別不能住在任何一邊。

**為什麼不能是普通的例外**：``execute_streaming`` 的收尾是
``except Exception → return self._error_result(...)``（非暫時性錯誤
fail-fast 的既有設計）。分岔訊號若被那層吃掉，claw_loop 的
``except ForkPointRequested`` 永遠不會執行——使用者拿到的不是分岔卡片，
而是一則錯誤訊息，而且是在跑了 15 個工具之後。所以 agent 那三處收尾
必須跟 CancelledError／SystemExit 一樣把它原樣上拋。
"""

from __future__ import annotations

from typing import List


class ForkPointRequested(Exception):
    """tool 步數觸及軟門檻（node 層攔截後轉 interrupt 卡片）。"""

    def __init__(self, steps: int, tools_used: List[str]):
        super().__init__(f"fork point at {steps} steps")
        self.steps = steps
        self.tools_used = list(tools_used)
