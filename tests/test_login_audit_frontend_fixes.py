"""登入鏈徹查（2026-09-10）前端修復釘子。

對應三份獨立 code review 的前端發現（檔案:行號見各測試）：
- WC-C1  死 QR 換發的程式化 close 觸發取消確認 → 1.2s 後殺死 flow＋收掉新 QR
- WC-H1  _ensureAppKit rejected promise 永久快取（一次暫時失敗＝整頁毒化）
- WC-H3  續登只軟重連——殭屍 socket 收不到 personal_sign
- WC-L2  iOS bfcache 下到期 timer 可能搶在 visibilitychange 前判死
- WC-L3  _notifySiwxLogin handler 未包 try/catch
- FE-H2  續登 catch 清旗標在 e.handled 之前（重載接手閉環斷裂）；
         續登收尾不佔 _evmLoginInFlight（與使用者點擊並行雙跳簽名）
- FE-H3  純取消也被踢續登 → 27 秒後誤導性紅色 toast
- FE-M1  簽名逾時重試重置前景重送預算（單次登入最多 6 發 personal_sign）
- FE-M2  續登進度寫進隱藏 modal（deep-link 回來全程無聲）
- FE-M3  api-client 錯誤（逾時/5xx/原始 JSON）直出英文技術名詞進 toast
- FE-L1  evm-nonce 不在 401 豁免清單
- FE-L2  經典路徑成功不清 WC_RESUME_FLAG
- FE-L3  siwxLoggedIn 路徑不關 login modal
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


WC = "web/js/evm-walletconnect.js"
AUTH = "web/js/evm-auth.js"


class TestDeadQrRegenRace:
    def test_reopen_cancels_pending_close_confirm(self):
        src = _read(WC)
        assert "let closeConfirmTimer = null;" in src
        assert re.search(r"if \(closeConfirmTimer\) \{\s*clearTimeout\(closeConfirmTimer\)", src), (
            "state.open=true（換發後重開）要取消排程中的取消確認"
        )
        assert "closeConfirmTimer = setTimeout" in src, "取消確認要追蹤 timer id"

    def test_regen_then_checks_settled(self):
        src = _read(WC)
        block = re.search(r"ensureRelayConnected\(appKit, \{ force: true \}\)\.then\(\(\) => \{(.*?)\}\);", src, re.S)
        assert block, "找不到換發 .then()"
        assert "if (settled) return;" in block.group(1), "換發回呼要重查 settled"


class TestAppKitInitRetry:
    def test_rejected_promise_not_cached_forever(self):
        src = _read(WC)
        block = re.search(r"function _ensureAppKit\(\) \{.*?\n\}", src, re.S)
        assert block
        assert "_initPromise.catch" in block.group(0), (
            "初始化失敗要清快取讓下次重試——永久快取 rejected promise 會讓"
            "弱網下一次失敗毒化整頁生命週期"
        )


class TestResumeForceReconnect:
    def test_resume_uses_force(self):
        src = _read(WC)
        block = re.search(r"async function resumeWalletConnectSession.*?\n\}", src, re.S)
        assert block
        assert re.search(r"ensureRelayConnected\(appKit, \{ force: true \}\)", block.group(0)), (
            "續登要 force 重開 relay——殭屍 socket 下 personal_sign 會靜默消失"
        )


class TestFinishReasons:
    def test_reasons_annotated_and_exported(self):
        src = _read(WC)
        assert "getConnectFinishReason" in src
        for reason in ("'cancel'", "'stalled'", "'timeout'"):
            assert f"finish(null, {reason})" in src, f"缺少 finish 原因 {reason}"

    def test_cancel_does_not_kick_resume(self):
        src = _read(AUTH)
        block = re.search(r"const finishReason = wcModule\.getConnectFinishReason.*?return false;", src, re.S)
        assert block, "conn=null 要先查 finish 原因"
        assert "finishReason !== 'cancel'" in block.group(0), (
            "使用者主動取消不得踢續登（27 秒後的紅色誤導 toast）"
        )


class TestDeadlineVisibilityGuard:
    def test_deadline_timer_defers_when_hidden(self):
        src = _read(WC)
        block = re.search(r"const armDeadline = \(ms\) => \{.*?\n\s*\};", src, re.S)
        assert block
        assert "document.visibilityState !== 'visible'" in block.group(0), (
            "到期 timer 在頁面不可見時要延期，不得搶在 visibilitychange 前判死"
        )


class TestSiwxHandlerGuard:
    def test_notify_siwx_wraps_handler(self):
        src = _read(WC)
        block = re.search(r"function _notifySiwxLogin.*?\n\}", src, re.S)
        assert block
        assert "try {" in block.group(0).split("_siwxOnLogin(result, address)")[0].split("forEach")[1], (
            "_siwxOnLogin 呼叫要包 try/catch"
        )


class TestResumeFlagAndParallel:
    def test_handled_keeps_resume_flag(self):
        src = _read(AUTH)
        block = re.search(r"async function _tryResumeWalletConnectLogin.*?\n\}\n", src, re.S)
        assert block
        body = block.group(0)
        handled = re.search(r"catch \(e\) \{(.*?)_setWcResumeFlag\(false\)", body, re.S)
        assert handled, "catch 區塊要在 e.handled 之後才清旗標"
        assert "e.handled" in handled.group(1), "e.handled 分支必須先於清旗標（保留旗標）"

    def test_resume_completion_occupies_inflight_and_opens_modal(self):
        src = _read(AUTH)
        block = re.search(r"async function _tryResumeWalletConnectLogin.*?\n\}\n", src, re.S)
        body = block.group(0)
        assert "_evmLoginInFlight = true" in body, "續登收尾要佔用防重入閘（防並行雙跳簽名）"
        assert "classList.remove('hidden')" in body, "續登進度要打開 login modal（deep-link 回來全程無聲）"


class TestSignResendBudget:
    def test_resender_created_once(self):
        src = _read(AUTH)
        block = re.search(r"const fgResender = viaWalletConnect.*?;\n", src, re.S)
        assert block, "resender 要建一次共用"
        assert re.search(r"signWait = viaWalletConnect\s*\?\s*\(ms\) => fgResender\.race\(ms\)", src), (
            "signWait 不得每次重建 resender（重送預算會歸零）"
        )


class TestErrorLocalization:
    def test_api_errors_use_localized_message(self):
        src = _read(AUTH)
        block = re.search(r"function _loginFailedMessage.*?\n\}\n", src, re.S)
        assert block, "登入失敗訊息集中在 _loginFailedMessage"
        body = block.group(0)
        assert "_t('evmAuth.verifyFailed'" in body
        assert re.search(r"e\.status\b[^\n]*return base;", body), (
            "帶 status 的 api-client 錯誤（逾時/5xx/JSON）要換在地化通用訊息"
        )

    def test_nonce_in_401_exempt_list(self):
        src = _read("web/js/api-client.js")
        assert "/api/user/evm-nonce" in src, "evm-nonce 要在 401 豁免清單"


class TestFlagAndModalHygiene:
    def test_classic_success_clears_resume_flag(self):
        src = _read(AUTH)
        block = re.search(r"async function _completeEvmLogin.*?\n\}\n", src, re.S)
        assert "_setWcResumeFlag(false)" in block.group(0), "經典路徑成功要清續登旗標"

    def test_siwx_logged_in_closes_modal(self):
        src = _read(AUTH)
        block = re.search(r"if \(conn\.siwxLoggedIn\) \{.*?\}", src, re.S)
        assert block
        assert "classList.add('hidden')" in block.group(0), "siwxLoggedIn 要關 login modal"
