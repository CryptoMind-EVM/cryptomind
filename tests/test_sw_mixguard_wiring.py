"""SW 混快取防護的接線回歸（2026-09-07 線上「reading 'add'」事故）。

背景：一天內多次部署讓 Service Worker 混用新舊 chunk——宣告
``window.selectedSessions``／``window.pendingTickerSymbols`` 等全域 Set
的模組沒執行到、消費端照呼叫 ``.add()/.size`` → TypeError。同一場事故
也造成瞬時 CSS 404（跑版）與元件半渲染。修法：消費端惰性補建
（``window.x = window.x || new Set()``）——不是修根因（SW 快取策略有
stale-chunk-recovery 兜底），是把這類崩潰變成無害降級。

本檔守住：三個消費函數的補建不得被移除＋i18n 新鍵四語系齊。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
CHAT_SESSIONS = REPO / "web" / "js" / "chat-sessions.js"
MARKET_WS = REPO / "web" / "js" / "market-ws.js"


def _fn_body(js: str, signature: str) -> str:
    start = js.index(signature)
    return js[start : js.index("\n}", start) + 2]


class TestGlobalSetLazyInit:
    def test_chat_sessions_guards(self):
        js = CHAT_SESSIONS.read_text(encoding="utf-8")
        for sig in (
            "async function loadSessionsOnce(opts)",  # loadSessions() 是合併並發呼叫的外層（2026-09-26）
            "function createSessionItem(session)",
            "function toggleSessionSelection(sessionId)",
        ):
            body = _fn_body(js, sig)
            assert "window.selectedSessions = window.selectedSessions || new Set()" in body, (
                f"{sig} 的惰性補建被移除——SW 混快取時 .size/.add 會 TypeError"
            )

    def test_market_ws_guards(self):
        js = MARKET_WS.read_text(encoding="utf-8")
        for sig in ("function subscribeTickerSymbols(symbols)",):
            body = _fn_body(js, sig)
            assert "window.pendingTickerSymbols = window.pendingTickerSymbols || new Set()" in body
            assert "window.subscribedTickerSymbols = window.subscribedTickerSymbols || new Set()" in body
        # onopen 處理器（arrow，無法用 _fn_body 切）直接驗存在
        onopen = js[js.index("window.marketWebSocket.onopen") : js.index("window.marketWebSocket.onmessage")]
        assert "pendingTickerSymbols || new Set()" in onopen


class TestAiStudioErrorCopy:
    """'no active preset' 是內部訊息，永遠不准原樣丟給使用者看。

    2026-09-08 起這句話拆成兩個成因（#682）：真的還沒建 Preset ＝
    agentsNoScope；讀不到（多半 session 過期）＝ sessionLoadFailedRetry。
    舊的 sessionOrPresetMissing 把兩件事塞在同一句，還把「登入已失效」擺
    前面，導致只是沒建 Preset 的人被導去重新登入——已移除。
    """

    def test_no_active_preset_is_branched_not_shown_raw(self):
        js = (REPO / "web" / "js" / "ai-studio.js").read_text(encoding="utf-8")
        assert "no active preset" in js, "分流條件不可移除"
        assert "_noScopeMessage()" in js, "人類可讀訊息不可移除"
        # 模型與工具兩條存檔路徑都要走分流，不能有一條漏掉
        assert js.count("_noScopeMessage()") >= 3

    def test_both_causes_have_distinct_copy(self):
        js = (REPO / "web" / "js" / "ai-studio.js").read_text(encoding="utf-8")
        assert "aiStudio.agentsNoScope" in js
        assert "aiStudio.sessionLoadFailedRetry" in js

    def test_i18n_all_locales(self):
        for loc in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{loc}.json").read_text(encoding="utf-8")
            )
            section = data.get("aiStudio", {})
            for key in ("agentsNoScope", "sessionLoadFailedRetry"):
                assert section.get(key), f"{loc}.json aiStudio.{key} 缺失"


class TestReownProjectIdSwapped:
    """2026-09-08 回切 af1d 專案：#673 換到的 9c88ef0c… 不在任何可及帳號下，
    網域驗證與 Explorer 上架都需要登得進後台的專案；af1d 帳號已尋回。"""

    def test_project_id_is_managed_account(self):
        js = (REPO / "web" / "js" / "evm-walletconnect.js").read_text(encoding="utf-8")
        assert "af1d2fb078465000943d5caa4959538e" in js
        # 只看可執行碼——註解裡留「為何回切」的歷史脈絡是好事
        code = "\n".join(
            line for line in js.splitlines() if not line.strip().startswith("//")
        )
        assert "9c88ef0c" not in code, "孤兒專案 ID 不得殘留於可執行碼"
