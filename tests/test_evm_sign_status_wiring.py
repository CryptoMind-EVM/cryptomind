"""簽名等待回饋的接線回歸（2026-09-05 二版：按鈕內建忙碌狀態）。

脈絡：personal_sign 等 150s＋自動重送 150s 期間 modal 必須持續回饋。
初版（PR #652）用 JS 動態插入琥珀色塊到 modal 底部——功能生效但
DANNY 回饋：「不是就這樣黃色版面…手機上怎麼辦」。等待簽署是進行中
資訊而非警告（DESIGN_SYSTEM 無 warning token，amber 僅留給警告），
二版改為：狀態吃進 #evm-login-btn（轉圈＋短文案），按鈕下一行 muted
小字帶細節——按鈕本來就 w-full 置中，手機自然適配。

本測試守住接線不被回退：
1. index.html 有齊靜態元素（spinner／雙 label／狀態行），初始隱藏。
2. evm-auth.js 只切 hidden——不得再出現 inline 警告色樣式或動態插塊。
3. 新 i18n key（signWaitingShort）與既有細節文案（signWaiting/signRetry）
   四語系齊備。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "web" / "index.html"
EVM_AUTH = REPO / "web" / "js" / "evm-auth.js"
LOCALES = ["en", "zh-TW", "zh-CN", "ru"]


class TestSignStatusWiring:
    """index.html 靜態接線：按鈕忙碌狀態元素 + 狀態行，預設全部隱藏。"""

    def test_button_has_busy_state_elements(self):
        html = INDEX.read_text(encoding="utf-8")
        for el_id in (
            "evm-login-icon",
            "evm-login-spinner",
            "evm-login-label",
            "evm-login-label-wait",
        ):
            assert f'id="{el_id}"' in html, f"index.html 缺 #{el_id}"

        # 轉圈與等待文案初始隱藏；spinner 要有 animate-spin 才看得出「在跑」
        spinner = html[html.index('id="evm-login-spinner"') : html.index("</svg>", html.index('id="evm-login-spinner"'))]
        assert "animate-spin" in spinner
        wait_label = html[html.index('id="evm-login-label-wait"') : html.index("</span>", html.index('id="evm-login-label-wait"'))]
        assert "hidden" in wait_label
        # 按鈕只放短句（7d0b36b：共用長句時按鈕變兩行、底下又重複一次）；長提示留給狀態列
        assert "evmAuth.signWaitingButton" in wait_label
        assert "evmAuth.signWaitingShort" not in wait_label

    def test_status_line_exists_and_hidden_by_default(self):
        html = INDEX.read_text(encoding="utf-8")
        start = html.index('id="evm-sign-status"')
        line = html[html.rindex("<div", 0, start) : html.index(">", start) + 1]
        assert "hidden" in line, "狀態行預設必須隱藏（_setSignStatus 切換）"
        assert "text-textMuted" in line, "進行中資訊用 muted，不用警告色"
        assert 'id="evm-sign-status-text"' in html

    def test_status_line_sits_next_to_login_button(self):
        """狀態行要跟在 #evm-login-btn 之後（同一登入區塊），不是 modal 底部。"""
        html = INDEX.read_text(encoding="utf-8")
        assert html.index('id="evm-login-btn"') < html.index('id="evm-sign-status"')
        assert (
            html.index('id="evm-sign-status"')
            < html.index("login.secureLogin")  # 信任指示器（原本就在按鈕後）
        ), "狀態行應在按鈕與信任指示器之間"


class TestSetSignStatusToggleOnly:
    """evm-auth.js：只切 hidden class，不得回退成 inline 樣式／動態插塊。
    （2026-09-06 三階段狀態機：_setSignStatus 統一把按鈕交給
    _setEvmLoginButtonPhase，狀態行自己管。）"""

    def test_no_inline_warning_style_injection(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        fn = js[js.index("function _setSignStatus") : js.index("\n}", js.index("function _setSignStatus")) + 2]
        assert "_setEvmLoginButtonPhase(textOrNull ? 'signing' : null)" in fn
        phase_fn = js[
            js.index("function _setEvmLoginButtonPhase") :
            js.index("\n}", js.index("function _setEvmLoginButtonPhase")) + 2
        ]
        assert "classList.toggle('hidden'" in phase_fn
        assert "aria-busy" in phase_fn, "忙碌狀態要同步 aria-busy（無障礙）"
        # 初版的琥珀色 inline 樣式與 createElement 插塊不得回來
        assert "245,158,11" not in fn + phase_fn
        assert "createElement" not in fn + phase_fn

    def test_status_cleared_on_all_exits(self):
        """finally 清狀態——成功／失敗／拒絕都不能把按鈕留在忙碌態。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        # befe523 起 requestSign 是 async 箭頭函式——錨在宣告名稱，不綁箭頭寫法
        sign_block = js[js.index("const requestSign =") : js.index("// 4. 後端驗章")]
        assert "_setSignStatus(null)" in sign_block


class TestConnectPhaseWiring:
    """2026-09-06「空窗期瘋狂點」：點擊瞬間反灰，所有出口復原。"""

    def test_button_has_connecting_label(self):
        html = INDEX.read_text(encoding="utf-8")
        seg = html[html.index('id="evm-login-label-connecting"') : html.index("</span>", html.index('id="evm-login-label-connecting"'))]
        assert "hidden" in seg, "connecting 文案預設隱藏"
        assert "evmAuth.connectingShort" in seg

    def test_flow_sets_connecting_immediately_and_restores_in_finally(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        fn = js[js.index("window.safeEvmLogin = async function") : js.index("\n};", js.index("window.safeEvmLogin = async function")) + 3]
        assert fn.index("_setEvmLoginButtonPhase('connecting')") < fn.index("discoverInjectedProviders"), (
            "connecting 要在 EIP-6963 探測（空窗期起點）之前就設上"
        )
        assert "_setEvmLoginButtonPhase(null)" in fn[fn.index("finally") :], (
            "finally 必須復原按鈕——任何出口不留忙碌態"
        )

    def test_i18n_connecting_all_locales(self):
        import json

        for loc in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{loc}.json").read_text(encoding="utf-8")
            )
            assert data.get("evmAuth", {}).get("connectingShort"), (
                f"{loc}.json evmAuth.connectingShort 缺失或為空"
            )


class TestSignStatusI18n:
    def test_all_locales_have_keys(self):
        for loc in LOCALES:
            data = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{loc}.json").read_text(encoding="utf-8")
            )
            evm_auth = data.get("evmAuth", {})
            for key in ("signWaitingShort", "signWaiting", "signRetry"):
                assert evm_auth.get(key), f"{loc}.json evmAuth.{key} 缺失或為空"
