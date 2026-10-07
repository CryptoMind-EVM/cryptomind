"""錢包選單「殘影 click」回歸（2026-09-08 DANNY iOS 錄影）。

症狀：iOS 上點「連接 EVM 錢包」直接跳去 MetaMask，錢包選單連畫都沒畫出來；
回到 MetaMask 內建瀏覽器再點又跳同一個連結，變成無限循環。

根因不是選單壞掉，是它被同一個手指「選走」了：click-delegator 在觸控時
於 pointerdown 就派發 safeEvmLogin（手指還壓在螢幕上），引導面板要等注入
錢包偵測 250ms 才插進 DOM——放手的原生 click 於是 hit-test 到剛出現的面板，
選中落點下的那一列。iOS 版面下登入按鈕正好壓在第一列（MetaMask）。
click-delegator 自己的手勢級吞點只保護 [data-click] 委派路徑，面板的列是
自己掛的 listener，攔不到。

守兩件事：
1. 判定純函式（shouldSwallowGuideClick）行為——由 tests/js/evm_mobile_guide.mjs
   的 node 斷言看守，這裡做 pytest 包裝，免得那支 .mjs 沒人跑。
2. evm-auth.js 真的把吞點掛在面板的 capture 階段（不是只匯入不用）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
EVM_AUTH = REPO / "web" / "js" / "evm-auth.js"
MOBILE_GUIDE = REPO / "web" / "js" / "evm-mobile-guide.js"


class TestGhostClickDecision:
    """純函式判定：由 node 斷言看守。"""

    def test_node_gate_passes(self):
        try:
            result = subprocess.run(
                ["node", "tests/js/evm_mobile_guide.mjs"],
                cwd=REPO,
                capture_output=True,
                text=True,
                # node 印中文；不指定就用 Windows 的 cp950 解碼 UTF-8 而炸掉
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            pytest.skip("node 不可用")
        assert result.returncode == 0, (
            "evm-mobile-guide 判定閘門不符預期：\n" + result.stdout + result.stderr
        )
        assert "all assertions passed" in result.stdout


class TestGhostClickWiring:
    """面板上真的掛了吞點——判定函式存在但沒接上等於沒修。"""

    def test_guide_installs_capture_phase_swallow(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        assert "shouldSwallowGuideClick" in js, "evm-auth.js 沒匯入殘影 click 判定"

        # 面板插進 DOM 之後、掛各列 listener 之前就要備妥吞點
        append_at = js.index("document.body.appendChild(overlay)")
        first_row_at = js.index("evm-open-in-wallet-option')", append_at)
        wiring = js[append_at:first_row_at]

        assert re.search(
            r"overlay\.addEventListener\(\s*'pointerdown'", wiring
        ), "要靠面板上的 pointerdown 認出「這一下是不是真的在選錢包」"
        assert "shouldSwallowGuideClick(" in wiring, "click 吞點沒接上判定函式"
        assert re.search(
            r"e\.stopPropagation\(\)", wiring
        ), "吞點必須擋住事件往下傳，否則各列自己的 listener 照跑"

        # capture 階段才攔得到——冒泡階段輪到面板時，列的 listener 已經跑完
        click_at = wiring.index("'click'")
        assert "true" in wiring[click_at:], "click 吞點必須掛在 capture 階段"

    def test_decision_helper_lives_in_pure_module(self):
        """判定留在不碰 DOM 的模組，node 才測得到。"""
        assert "export function shouldSwallowGuideClick" in MOBILE_GUIDE.read_text(
            encoding="utf-8"
        )
