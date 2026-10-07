"""登出真斷錢包連線的接線回歸（2026-09-05 DANNY「要徹底解決」）。

背景：兩層分離（網站 session／AppKit-WC 連線）下，登出後 AppKit 面板仍
顯示「已連接」，站長回報矛盾（「不是都登出了嗎」）。拍板：登出＝錢包
連線一起真斷（docs/plans/2026-09-05-logout-wallet-disconnect-design.md）。

機制（跨 4 檔的鏈路，任一環斷了整個閉環失效——本測試逐環看守）：

  auth.js logout()
    └─ 動態 import ./evm-auth.js（不得靜態：auth.js 也被論壇頁載入）
         └─ prepareEvmWalletForLogout()
              ├─ applyLogoutDisconnectFlags()  廢續登旗標＋設待斷線旗標（順序即語義）
              └─ 裸 import evm-walletconnect.disconnectWalletConnect()
                   └─ AppKit 未初始化→回 false（旗標留著）；有 session→appKit.disconnect()

  下次頁面載入：有待斷線旗標才預載 evm-auth._warmWalletConnect()
  （2026-09-27 起 AppKit 不再無條件預載，改使用者意圖觸發）
    └─ _consumePendingDisconnect()  消化旗標，斷線收尾（閉環）

行為面（旗標順序、邊界）由 tests/js/evm_wc_recovery.mjs 的 Node 斷言看守；
本檔只守「接線存在且關鍵順序正確」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
AUTH = REPO / "web" / "js" / "auth.js"
EVM_AUTH = REPO / "web" / "js" / "evm-auth.js"
EVM_WC = REPO / "web" / "js" / "evm-walletconnect.js"
FEEDBACK = REPO / "web" / "js" / "evm-login-feedback.js"


def _fn_body(js: str, signature: str) -> str:
    start = js.index(signature)
    end = js.index("\n}", start) + 2
    return js[start:end]


class TestAuthLogoutWiring:
    def test_logout_disconnects_evm_wallet(self):
        """logout 要真的斷 EVM 錢包連線（TON Connect 已在 2026-09-26 整個移除）。"""
        js = AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "logout()")
        assert "tonConnectUI" not in body, "TON Connect 已移除，不該再碰"
        assert "import('./evm-auth.js')" in body, "EVM 斷線經動態 import（論壇頁相容）"
        assert "prepareEvmWalletForLogout" in body

    def test_logout_not_blocked_by_disconnect(self):
        """斷線是 fire-and-forget——logout 內不得 await 斷線（登出必須即時）。"""
        js = AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "logout()")
        assert "await" not in body, "logout 是同步方法；斷線絕不能延遲登出"


class TestEvmAuthOrchestration:
    def test_prepare_sets_flags_before_import(self):
        """旗標操作必須在動態 import 之前——reload 可能隨時截斷後者。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "export function prepareEvmWalletForLogout()")
        assert "applyLogoutDisconnectFlags" in body
        assert body.index("applyLogoutDisconnectFlags") < body.index("import("), (
            "旗標先設、import 後跑——順序反了會在被 reload 截斷時丟失意圖"
        )
        # 確認斷線完成才清旗標
        assert "done === true" in body

    def test_prepare_uses_bare_import_not_stale_chunk_recovery(self):
        """_loadWalletConnect 的過期 chunk 救濟會整頁 reload，攔截登出 POST。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "export function prepareEvmWalletForLogout()")
        assert "_loadWalletConnect" not in body
        assert "import('./evm-walletconnect.js')" in body

    def test_boot_consumes_pending_flag_after_preload(self):
        """收尾必須在 preload 之後——否則未初始化防護回 false、旗標卻被清
        （閉環斷裂，session 殘留——實作第一版就踩到這個競態）。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        warm = _fn_body(js, "function _warmWalletConnect()")
        assert "_consumePendingDisconnect()" in warm
        assert warm.index("preloadAppKit") < warm.index("_consumePendingDisconnect"), (
            "consume 必須排在 preload 完成之後（.then 鏈內）"
        )
        consume = _fn_body(js, "async function _consumePendingDisconnect()")
        assert "hasPendingDisconnect" in consume
        assert "done === true" in consume, "只有確認斷線完成才清旗標"


class TestWalletConnectDisconnect:
    def test_guard_when_appkit_never_initialized(self):
        """AppKit 未初始化→回 false 不下載 7MB——persisted session 交給旗標收尾。"""
        js = EVM_WC.read_text(encoding="utf-8")
        body = _fn_body(js, "async function disconnectWalletConnect()")
        assert "if (!_initPromise) return false" in body
        assert "readWcSession(appKit)" in body, "沒有活 session 時視為乾淨，不誤炸"

    def test_exported(self):
        js = EVM_WC.read_text(encoding="utf-8")
        export_block = js[js.index("export {") :]
        assert "disconnectWalletConnect" in export_block


class TestFeedbackHelpers:
    def test_key_names_single_source_of_truth(self):
        """鍵名常數只在 evm-login-feedback 定義一次；evm-auth 一律 import 常數
        （防手改不同步），續登旗標 key 是 evm-auth 既有字面值——兩邊都要對上。"""
        fb = FEEDBACK.read_text(encoding="utf-8")
        evm = EVM_AUTH.read_text(encoding="utf-8")
        assert "evmWcResumeAt" in fb and "evmWcPendingDisconnect" in fb
        assert "WC_PENDING_DISCONNECT_KEY" in evm, "待斷線鍵必須用常數，不得重複字面值"
        # evm-auth 的續登旗標 key（既有字面值）也要與 feedback 常數一字不差
        assert "const WC_RESUME_FLAG = 'evmWcResumeAt'" in evm
