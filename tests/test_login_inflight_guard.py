"""「登入進行中」旗標的寫入端必須存在（2026-09-08）。

背景：``AuthManager.shouldDeferExpiredSessionCleanup()`` 與 toolSettings 的
401 重試都靠一個全域旗標判斷「現在正在登入，別把過期 session 清掉／別當成
真的沒登入」。

這個機制在 2026-08-31 登入改走 EVM 之後**斷了半個月**：唯一的寫入者是
``ton-auth.js`` 的 ``safeTonLogin``，而 TON 登入入口那天就被移除了。兩個讀取端
都還在，只是永遠讀到 ``undefined``。EVM 登入用的 ``_evmLoginInFlight`` 是模組內
變數，從來沒掛到 window 上。

**沒有任何測試守它，所以沒人發現。** 這支就是補上那個缺口——讀取端與寫入端
任一邊被改掉、或旗標名稱又漂移，這裡就會紅。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
EVM_AUTH = (ROOT / "web/js/evm-auth.js").read_text(encoding="utf-8")
AUTH = (ROOT / "web/js/auth.js").read_text(encoding="utf-8")
TOOL_SETTINGS = (ROOT / "web/js/toolSettings.js").read_text(encoding="utf-8")

WINDOW_FLAG = "__loginInFlight"
STORE_KEY = "loginInProgress"


class TestWriterExists:
    """沒有寫入端的守衛等於沒有守衛。"""

    def test_evm_auth_defines_the_setter(self):
        assert "function _setLoginInFlight(" in EVM_AUTH

    def test_setter_writes_both_channels(self):
        body = EVM_AUTH.split("function _setLoginInFlight(", 1)[1].split("\n}", 1)[0]
        assert f"window.{WINDOW_FLAG}" in body
        assert f"AppStore.set('{STORE_KEY}'" in body

    def test_interactive_login_sets_and_clears_it(self):
        login = EVM_AUTH.split("window.safeEvmLogin = async function (", 1)[1]
        assert "_setLoginInFlight(true)" in login, "登入開始沒有立旗標"
        assert "_setLoginInFlight(false)" in login, "登入結束沒有清旗標"

    def test_background_resume_sets_and_clears_it(self):
        # 續登也是登入：deep-link 回來那段同樣不能被清 session
        resume = EVM_AUTH.split("async function _tryResumeWalletConnectLogin(", 1)[1]
        assert "_setLoginInFlight(true)" in resume
        assert "_setLoginInFlight(false)" in resume

    def test_clear_happens_in_finally(self):
        # 只在成功路徑清會讓失敗的登入把旗標永遠留在 true，
        # 之後所有過期 session 都不會被清掉
        login = EVM_AUTH.split("window.safeEvmLogin = async function (", 1)[1]
        finally_block = login.split("} finally {", 1)[1].split("}", 1)[0]
        assert "_setLoginInFlight(false)" in finally_block


class TestReadersMatchTheWriter:
    """讀取端與寫入端必須講同一個名字——上次就是名字對不上才壞掉。"""

    def test_auth_manager_reads_the_same_flag(self):
        # 讀旗標集中在 isLoginInFlight（通知服務也用它）；切在「定義」而不是呼叫點
        reader = AUTH.split("isLoginInFlight() {", 1)[1].split("\n    },", 1)[0]
        assert f"window.{WINDOW_FLAG}" in reader
        assert f"AppStore.get('{STORE_KEY}')" in reader
        guard = AUTH.split("shouldDeferExpiredSessionCleanup() {", 1)[1].split("\n    },", 1)[0]
        assert "this.isLoginInFlight()" in guard

    def test_tool_settings_reads_the_same_flag(self):
        assert f"window.{WINDOW_FLAG}" in TOOL_SETTINGS
        assert f"AppStore.get('{STORE_KEY}')" in TOOL_SETTINGS


class TestDeadTonFlagIsGone:
    """舊名不准回來——它是 TON 登入的遺骸，留著只會讓人以為守衛還活著。"""

    @pytest.mark.parametrize("src_name", ["evm-auth.js", "auth.js", "toolSettings.js"])
    def test_no_executable_reference_to_the_ton_flag(self, src_name):
        src = (ROOT / "web/js" / src_name).read_text(encoding="utf-8")
        # 只看實際取值的地方，註解裡留著歷史脈絡是好事
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        assert not re.search(r"window\._tonLoginInProgress", code), src_name
        assert not re.search(r"""['"]tonLoginInProgress['"]""", code), src_name
