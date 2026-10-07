"""Node 端 JS 決策閘門（tests/js/evm_wc_recovery.mjs）的 pytest 包裝。

與 tests/test_web_static_markup.py 的 node 閘門同一模式。argv 全為字面
常數、清單形式執行；node 不存在時以 FileNotFoundError 分流為 skip——
CI 有 node，本機沒有也不該擋住整套測試。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _run_node_gate(script: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["node", script],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("node 不可用")


class TestEvmWcRecoveryGates:
    def test_recovery_decisions(self):
        result = _run_node_gate("tests/js/evm_wc_recovery.mjs")
        assert result.returncode == 0, (
            "evm-wc 對策決策閘門行為不符預期：\n" + result.stdout + result.stderr
        )
        assert "all assertions passed" in result.stdout


class TestSocialLoginWatchdog:
    """2026-10-05：Email／Google 登入偶爾一直轉圈、不會跳回畫面——看門狗收掉並讓使用者重試。"""

    def test_watchdog_behaviour(self):
        result = _run_node_gate("tests/js/social_login_watchdog.mjs")
        assert result.returncode == 0, (
            "社群登入看門狗行為不符預期：\n" + result.stdout + result.stderr
        )
        assert "all assertions passed" in result.stdout

    def test_wiring(self):
        """行為在 node 閘門測；這裡守接線不被改掉：事件流→看門狗→finish→evm-auth 提示。"""
        wc = (REPO / "web/js/evm-walletconnect.js").read_text(encoding="utf-8")
        auth = (REPO / "web/js/evm-auth.js").read_text(encoding="utf-8")
        assert "createSocialLoginWatchdog" in wc
        assert "appKit.subscribeEvents" in wc
        assert "finish(null, 'social-stalled')" in wc
        assert "reportWcConnectEvent('social-stalled'" in wc
        # 錢包登入不受影響：只在社群模式初始化時才建看門狗
        assert re.search(r"_socialLoginAtInit\s*\?\s*createSocialLoginWatchdog", wc)
        # 登入與綁定兩條路徑都要處理，且卡死不踢續登
        assert auth.count("_notifySocialStalled()") >= 3  # 定義 + 登入 + 綁定
        assert "finishReason === 'social-stalled'" in auth
        for lang in ("zh-TW", "en", "ru", "zh-CN"):
            i18n = (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            assert '"socialStalled"' in i18n, lang
