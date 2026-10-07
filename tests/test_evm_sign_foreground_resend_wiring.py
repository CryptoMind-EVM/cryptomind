"""前景重送簽名的接線回歸（2026-09-05 Trust 三連漏實證後的機制修正）。

背景：手機 WC 流程中錢包 App 背景凍結會漏掉 relay 上的 personal_sign
（2026-09-05 14:45–14:51 線上：三發兩逾時，最後一發恰好在錢包醒著時送達
才彈——使用者用的是 Trust，倉庫 2026-09-01/02 實測已記錄 Trust pairing
問題）。機制修正：偵測 visibilitychange→visible（＝錢包剛被喚醒過）就
立刻重發，不再呆等 150s 逾時。

行為判定（shouldResendSignOnForeground）由 tests/js/evm_wc_recovery.mjs
的 Node 斷言看守；本檔守「機制接線」：

  evm-auth._makeForegroundSignResender
    ├─ visibilitychange 監聽（settle 時移除——不得漏監聽）
    ├─ 重發經 shouldResendSignOnForeground（有上限）
    ├─ latest-wins：重發的 promise 也要 attach 到 settle（否則使用者簽了
    │  重發的提示、我們等的卻是舊 promise——永遠等不到）
    └─ EVM_TIMEOUT 錯誤碼與 _withTimeout 一致（上層 catch 分流依賴它）
  _completeEvmLogin 簽名段
    └─ viaWalletConnect 走 resender；injected 直連維持 _withTimeout
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
EVM_AUTH = REPO / "web" / "js" / "evm-auth.js"
FEEDBACK = REPO / "web" / "js" / "evm-login-feedback.js"
USER_ROUTER = REPO / "api" / "routers" / "user.py"


def _fn_body(js: str, signature: str) -> str:
    start = js.index(signature)
    end = js.index("\n}", start) + 2
    return js[start:end]


class TestForegroundResenderWiring:
    def test_listener_added_and_removed(self):
        """監聽要在 race 開始掛上、settle 移除——漏移除會在登入後持續重發。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "function _makeForegroundSignResender")
        assert "addEventListener('visibilitychange'" in body
        assert "removeEventListener('visibilitychange'" in body
        # 移除發生在 settle 內（所有出口都會走到）
        settle = body[body.index("const settle") : body.index("const attach")]
        assert "removeEventListener" in settle

    def test_resend_gated_by_pure_decision(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "function _makeForegroundSignResender")
        assert "shouldResendSignOnForeground" in body
        assert "sign-resent" in body, "重發要發遙測（事後判讀）"

    def test_latest_wins_attach(self):
        """重發的 promise 必須 attach 到同一個 settle——latest-wins 是本機制的
        正確性核心（RPC id 各自獨立，await 舊 promise 會永遠等不到）。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "function _makeForegroundSignResender")
        listener_seg = body[body.index("listener = () =>") : body.index("document.addEventListener")]
        assert "attach(requestSign())" in listener_seg, "重發後要 attach 新 promise"

    def test_timeout_error_code_matches_with_timeout(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "function _makeForegroundSignResender")
        assert "EVM_TIMEOUT" in body, "逾時錯誤碼要與 _withTimeout 一致（上層分流依賴）"

    def test_sign_block_uses_resender_only_for_wc(self):
        js = EVM_AUTH.read_text(encoding="utf-8")
        seg = js[js.index("const fgResender = viaWalletConnect") : js.index("let signature;")]
        # 2026-09-10 徹查：resender 建一次共用（signWait 每次重建會把重送
        # 預算歸零——逾時重試那一輪又有全新預算，單次登入最多 6 發簽名請求）
        assert "_makeForegroundSignResender(requestSign)" in seg, (
            "resender 只能建一次（fgResender），signWait 共用它"
        )
        assert "fgResender.race(ms)" in seg, "WC 路徑走共用的前景重送 race"
        assert "_withTimeout" in seg, "injected 直連維持原逾時（無凍結漏包問題）"


class TestTelemetryAndI18n:
    def test_api_mode_enum_includes_sign_resent(self):
        py = USER_ROUTER.read_text(encoding="utf-8")
        assert '"sign-resent"' in py, "遙測端點要收新 mode，否則 beacon 422 靜默丟失"

    def test_i18n_all_locales_have_sign_resent(self):
        import json

        for loc in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{loc}.json").read_text(encoding="utf-8")
            )
            assert data.get("evmAuth", {}).get("signResent"), (
                f"{loc}.json evmAuth.signResent 缺失或為空"
            )

    def test_pure_decision_max_constant(self):
        fb = FEEDBACK.read_text(encoding="utf-8")
        assert "SIGN_FOREGROUND_RESEND_MAX" in fb
