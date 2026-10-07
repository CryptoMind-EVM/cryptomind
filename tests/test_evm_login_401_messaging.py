"""EVM 登入失敗路徑的使用者可見行為（2026-09-10 DANNY 朋友 iOS 回報
「跳到錢包後點連接錢包都沒有用」徹查的兩個可修缺陷）。

線上重現（iPhone 模擬＋注入假 MetaMask）證實：
1. evm-login 回 401（簽章被拒／nonce 過期）時，api-client 的全域 401
   攔截器先打 /api/user/refresh，再把伺服器的真實 detail 換成
   「登入已過期，請重新整理頁面」——對剛要登入的人是誤導（沒有 session
   可刷新；正確指引是再試一次）。
2. 注入直連路徑（錢包內建瀏覽器）personal_sign 前不設「等待錢包簽署」
   狀態（只有 viaWalletConnect 設）——150 秒等待期按鈕只顯示
   「連接錢包中…」，使用者不知道流程在等錢包彈窗。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestLoginEndpoint401NotMasked:
    def test_exempt_list_covers_evm_login(self):
        src = _read("web/js/api-client.js")
        m = re.search(r"AUTH_401_[A-Z_]*\s*=\s*\[[^\]]*\]", src)
        assert m, "找不到 401 豁免清單（AUTH_401_*）"
        assert "/api/user/evm-login" in m.group(0), "evm-login 必須在 401 豁免清單"

    def test_401_refresh_branch_checks_exempt_list(self):
        """401→refresh→retry 分支必須先檢查豁免清單，登入端點直出伺服器 detail。"""
        src = _read("web/js/api-client.js")
        branch = re.search(r"response\.status === 401[^{]*\{", src)
        assert branch, "找不到 401 分支"
        cond = re.search(
            r"if\s*\(\s*response\.status === 401\s*&&\s*!_isLoginEndpoint\(url\)", src
        )
        assert cond, "401 分支條件需含 !_isLoginEndpoint(url)"

    def test_is_login_endpoint_helper_exists(self):
        src = _read("web/js/api-client.js")
        assert re.search(r"function _isLoginEndpoint\(url\)", src), (
            "需要 _isLoginEndpoint(url) 判定（evm-login/ton-login 等）"
        )


class TestDirectConnectSignStatus:
    def test_sign_status_not_gated_on_walletconnect(self):
        """直連路徑 personal_sign 前也要設簽名等待狀態。"""
        src = _read("web/js/evm-auth.js")
        block = re.search(r"async function _completeEvmLogin.*?\n\}", src, re.S)
        assert block, "找不到 _completeEvmLogin"
        body = block.group(0)
        # 舊版：if (viaWalletConnect) { _setSignStatus(...) } —— 直連沒提示
        assert not re.search(r"if \(viaWalletConnect\) \{\s*_setSignStatus", body), (
            "_setSignStatus 不得只在 viaWalletConnect 分支設定"
        )
        assert "_setSignStatus(" in body, "personal_sign 前要設等待狀態"

    def test_sign_status_cleared_unconditionally(self):
        src = _read("web/js/evm-auth.js")
        block = re.search(r"async function _completeEvmLogin.*?\n\}", src, re.S)
        body = block.group(0)
        m = re.search(r"finally \{\s*(.*?)\}", body, re.S)
        assert m, "找不到 finally"
        assert not re.search(r"if \(viaWalletConnect\)", m.group(1)), (
            "finally 復位不得只限 viaWalletConnect——直連也要清狀態"
        )
        assert "_setSignStatus(null)" in m.group(1)

    def test_sign_waiting_direct_i18n_keys(self):
        """直連路徑重用既有 evmAuth.signWaitingShort（四語已內建）。"""
        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = _read(f"web/js/i18n/{lang}.json")
            assert "signWaitingShort" in data, (
                f"{lang}.json 缺 evmAuth.signWaitingShort"
            )
        src = _read("web/js/evm-auth.js")
        assert "evmAuth.signWaitingShort" in src, "直連路徑要用 signWaitingShort"


class TestPersonalSignEncoding:
    """Base App／Coinbase Wallet 原生 provider 只認 hex 的 personal_sign（純字串回「Invalid message」，
    2026-09-13 手機實測）；先 hex、非使用者拒簽才退回純字串，錯誤訊息帶錢包錯誤碼。"""

    def test_hex_first_then_plain_fallback(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1] / "web" / "js" / "evm-auth.js"
        ).read_text(encoding="utf-8")
        body = src.split("const requestSign = async () => {")[1].split(
            "const fgResender"
        )[0]
        assert "signOnce(_utf8ToHex(challenge.message))" in body
        assert "if (_isUserRejection(e)) throw e;" in body
        assert "signOnce(challenge.message)" in body
        assert "[personal_sign" in body
        assert "function _utf8ToHex(str)" in src and "new TextEncoder().encode" in src
