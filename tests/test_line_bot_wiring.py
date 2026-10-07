"""LINE Bot 接入的接線與安全守門（2026-09-08）。

design: docs/plans/2026-09-08-line-bot-shared-sessions-design.md

守住三件會直接出事的事：

1. **webhook 驗簽**：這是公開端點，簽章是唯一的身分證明。必須對**原始
   bytes** 做 HMAC-SHA256+base64，重新序列化過的 JSON 幾乎一定對不上。
2. **只能用 reply，不能用 push**：LINE 的 pricing 把 push 計入月額度
   （免費方案 200 則/月），reply 不計。哪天有人為了「比較好寫」改成 push，
   每一次 AI 回答都會燒付費額度，服務直接不可行。
3. **總開關**：LINE_CHANNEL_SECRET 未設 ⇒ 全部 404。沒設定就等於功能
   不存在，回滾不必改碼。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
LINE_ROUTER = REPO / "api" / "routers" / "line_link.py"
TG_CHAT = REPO / "api" / "routers" / "telegram_chat.py"
SCHEMA = REPO / "core" / "database" / "schema.py"
DB_INIT = REPO / "core" / "database" / "__init__.py"

SECRET = "test-channel-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


# ============================================================================
# 1. 驗簽
# ============================================================================


class TestSignature:
    def test_valid_signature_accepted(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        from api.routers.line_link import verify_line_signature

        body = b'{"events":[]}'
        assert verify_line_signature(body, _sign(body)) is True

    def test_wrong_signature_rejected(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        from api.routers.line_link import verify_line_signature

        body = b'{"events":[]}'
        assert verify_line_signature(body, _sign(body, "other-secret")) is False

    def test_missing_signature_rejected(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        from api.routers.line_link import verify_line_signature

        assert verify_line_signature(b'{"events":[]}', None) is False
        assert verify_line_signature(b'{"events":[]}', "") is False

    def test_tampered_body_rejected(self, monkeypatch):
        """簽原文、送改過的 body——這正是驗簽要擋的攻擊。"""
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        from api.routers.line_link import verify_line_signature

        original = b'{"events":[{"type":"message"}]}'
        sig = _sign(original)
        assert verify_line_signature(original + b" ", sig) is False

    def test_no_secret_configured_rejects_everything(self, monkeypatch):
        """沒設 secret 時不能變成「一律通過」。"""
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "")
        from api.routers.line_link import verify_line_signature

        body = b'{"events":[]}'
        assert verify_line_signature(body, _sign(body)) is False

    def test_signature_uses_constant_time_compare(self):
        """時間差可以一個 byte 一個 byte 反推出簽章。"""
        src = LINE_ROUTER.read_text(encoding="utf-8")
        fn = re.search(
            r"def verify_line_signature.*?(?=\ndef |\nclass |\n# ===)", src, re.S
        )
        assert fn, "找不到 verify_line_signature"
        assert "compare_digest" in fn.group(0)
        assert "==" not in fn.group(0).split("return")[-1], (
            "簽章不可用 == 比對——請用 hmac.compare_digest"
        )


# ============================================================================
# 2. reply，不是 push（成本存亡）
# ============================================================================


class TestReplyNotPush:
    def test_router_never_calls_push_endpoints(self):
        src = LINE_ROUTER.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        for billed in (
            "/message/push",
            "/message/multicast",
            "/message/broadcast",
            "/message/narrowcast",
        ):
            assert billed not in code, (
                f"用到 {billed}——這會計入 LINE 月額度（免費方案 200 則/月），"
                "每次 AI 回答都要錢。回覆一律走 /message/reply。"
            )

    def test_router_uses_reply_endpoint(self):
        src = LINE_ROUTER.read_text(encoding="utf-8")
        assert "/message/reply" in src
        assert "replyToken" in src

    def test_timeout_falls_back_to_reply_not_push(self):
        """超過 reply token 預算時要在預算內回一句導向網頁，而不是改用 push。"""
        src = LINE_ROUTER.read_text(encoding="utf-8")
        block = re.search(r"except asyncio\.TimeoutError:.*?return", src, re.S)
        assert block, "找不到逾時處理"
        # 剝掉註解再比對——否則會命中解釋「為什麼不用 push」的那行註解本身。
        code = "\n".join(
            line
            for line in block.group(0).splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "reply_text" in code
        assert "push" not in code.lower()

    def test_reply_budget_is_under_line_one_minute_limit(self, monkeypatch):
        """官方保證 reply token 只有 60 秒，預算必須留餘裕。"""
        from api.routers.line_link import REPLY_TOKEN_BUDGET_SECONDS

        assert 0 < REPLY_TOKEN_BUDGET_SECONDS < 60

    def test_loading_seconds_is_multiple_of_five(self):
        """官方 loadingSeconds 只接受 5 的倍數（5–60），送錯直接 400。"""
        from api.routers.line_link import LOADING_ANIMATION_SECONDS

        assert LOADING_ANIMATION_SECONDS % 5 == 0
        assert 5 <= LOADING_ANIMATION_SECONDS <= 60


# ============================================================================
# 3. 總開關
# ============================================================================


class TestKillSwitch:
    def test_disabled_without_secret(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        import api.routers.line_link as mod

        monkeypatch.setattr(mod, "_LINE_CHANNEL_SECRET", "")
        assert mod._line_enabled() is False

    def test_enabled_with_secret(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        import api.routers.line_link as mod

        assert mod._line_enabled() is True

    def test_kill_switch_raises_404_not_500(self, monkeypatch):
        """關閉時要像「這個端點不存在」，不是丟錯誤。"""
        from fastapi import HTTPException

        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        import api.routers.line_link as mod

        monkeypatch.setattr(mod, "_LINE_CHANNEL_SECRET", "")
        with pytest.raises(HTTPException) as exc:
            mod._require_line_enabled()
        assert exc.value.status_code == 404


# ============================================================================
# 4. 資料層與路由註冊
# ============================================================================


class TestWiring:
    def test_line_bindings_table_in_schema(self):
        src = SCHEMA.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS line_bindings" in src
        block = src.split("line_bindings")[1]
        # active_session_id 是「LINE 與網頁共用同一份對話」的關鍵欄位
        assert "active_session_id" in block
        assert "line_user_id TEXT PRIMARY KEY" in block, (
            "LINE userId 是 33 字元字串，不是 Telegram 那種數字 id"
        )
        assert "ON DELETE CASCADE" in block

    def test_db_helpers_exported(self):
        src = DB_INIT.read_text(encoding="utf-8")
        for name in (
            "create_line_binding",
            "get_binding_by_line_user_id",
            "get_line_binding_by_user_id",
            "update_line_last_used",
            "get_line_active_session",
            "set_line_active_session",
            "delete_line_binding",
            "delete_line_binding_by_user_id",
        ):
            assert f'"{name}"' in src, f"core.database 未匯出 {name}"

    def test_routes_registered(self):
        from api_server import app

        paths = set(app.openapi()["paths"])
        for p in (
            "/api/line/webhook",
            "/api/line/link-token",
            "/api/line/status",
            "/api/line/unlink",
        ):
            assert p in paths, f"{p} 沒註冊"

    def test_shared_chat_pipeline_not_duplicated(self):
        """LINE 走共用的 run_bot_chat，不是自己複製一份兩百行的管線。"""
        tg = TG_CHAT.read_text(encoding="utf-8")
        line = LINE_ROUTER.read_text(encoding="utf-8")
        assert "async def run_bot_chat(" in tg
        assert "run_bot_chat" in line
        assert "def run_telegram_chat" in tg, "Telegram 的入口不能被改掉"

    def test_line_uses_own_session_namespace(self):
        """跨平台 session 前綴不能撞——撞了會串到 Telegram 的對話。"""
        from api.routers.line_link import _line_default_session_id

        assert _line_default_session_id("U123").startswith("line:")

    def test_link_token_reuses_platform_agnostic_helpers(self):
        """link token 的產生／驗證本來就只綁 user_id，不該再寫第二份。"""
        line = LINE_ROUTER.read_text(encoding="utf-8")
        assert "from api.routers.telegram_link import (" in line
        assert "generate_link_token" in line
        assert "verify_link_token" in line
        assert "_is_link_token_consumed" in line, "少了重放防護"

    def test_background_tasks_are_strongly_referenced(self):
        """asyncio.create_task 只持弱引用，沒存起來會被 GC 中途收掉。"""
        import api.routers.line_link as mod

        assert isinstance(mod._BACKGROUND_TASKS, set)
        src = LINE_ROUTER.read_text(encoding="utf-8")
        assert "_BACKGROUND_TASKS.add(task)" in src
        assert "add_done_callback" in src


# ============================================================================
# 5. webhook 端到端（含驗簽）
# ============================================================================


class TestWebhookEndpoint:
    async def test_bad_signature_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        body = json.dumps({"events": []}).encode()
        resp = await client.post(
            "/api/line/webhook",
            content=body,
            headers={
                "X-Line-Signature": "deadbeef",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401

    async def test_valid_signature_empty_events_returns_200(self, client, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
        body = json.dumps({"events": []}).encode()
        resp = await client.post(
            "/api/line/webhook",
            content=body,
            headers={
                "X-Line-Signature": _sign(body),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def _text_event(user: str, text: str, token: str) -> dict:
    return {
        "type": "message",
        "message": {"type": "text", "text": text},
        "source": {"type": "user", "userId": user},
        "replyToken": token,
    }


class TestProcessEventsConcurrency:
    """一個 webhook 多人訊息：不同人並行（reply token 只有 60 秒），同一人照順序。"""

    async def test_different_users_run_concurrently_same_user_in_order(
        self, monkeypatch
    ):
        import asyncio

        import api.routers.line_link as mod

        log: list[tuple[str, str]] = []
        release = asyncio.Event()

        async def _fake_handle(line_user_id, text, reply_token, display_name):
            log.append(("start", reply_token))
            if reply_token == "a1":
                await release.wait()
            log.append(("end", reply_token))

        monkeypatch.setattr(mod, "_handle_text_message", _fake_handle)
        events = [
            _text_event("A", "q1", "a1"),
            _text_event("A", "q2", "a2"),
            _text_event("B", "q3", "b1"),
            {"type": "follow", "source": {"type": "user", "userId": "C"}},
        ]
        task = asyncio.create_task(mod._process_events(events))
        for _ in range(20):
            await asyncio.sleep(0)
        # A 的第一則還卡著，B 已經跑完——以前要排在 A 的兩則後面
        assert ("end", "b1") in log
        assert ("start", "a2") not in log, "同一人的第二則不能跟第一則同時跑"
        release.set()
        await asyncio.wait_for(task, timeout=1)
        a_order = [e for e in log if e[1] in ("a1", "a2")]
        assert a_order == [("start", "a1"), ("end", "a1"), ("start", "a2"), ("end", "a2")]

    async def test_one_failure_does_not_block_others(self, monkeypatch):
        import api.routers.line_link as mod

        handled: list[str] = []

        async def _fake_handle(line_user_id, text, reply_token, display_name):
            if reply_token == "a1":
                raise RuntimeError("boom")
            handled.append(reply_token)

        monkeypatch.setattr(mod, "_handle_text_message", _fake_handle)
        await mod._process_events(
            [
                _text_event("A", "q1", "a1"),
                _text_event("A", "q2", "a2"),
                _text_event("B", "q3", "b1"),
                "not-a-dict",
            ]
        )
        assert sorted(handled) == ["a2", "b1"]


# ============================================================================
# 連結卡的刷新機制（2026-09-09 DANNY 綁定踩坑後審查）
# ============================================================================

LINE_LINK_JS = REPO / "web" / "js" / "line-link.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _js() -> str:
    return LINE_LINK_JS.read_text(encoding="utf-8")


class TestLinkCardRefreshMechanics:
    """三個真實踩過的坑，鎖死修法不被改回去。

    1. 手機切去 LINE app 後 setInterval 被節流——tick 遞減式倒數會落後
       真實時間，畫面顯示「還沒過期」但碼早死了，複製出去必吃「已過期」。
    2. 倒數歸零同時殺掉輪詢——碼在最後一刻送出、綁定晚幾秒落地，
       網頁就永遠翻不成「已綁定」。
    3. 「我已送出」／分頁再進入把還有效的說明卡整個洗掉——碼消失、
       輪詢停止，使用者被迫重產一組。
    """

    def test_countdown_uses_wall_clock_not_tick_decrement(self):
        js = _js()
        assert "_tokenExpiresAt = Date.now()" in js, (
            "倒數必須以牆鐘到期時刻計算，不得依賴 setInterval 的 tick 次數"
        )
        countdown = re.search(r"_startCountdown\(\)\s*\{(.*?)\n    \}", js, re.S)
        assert countdown, "找不到 _startCountdown"
        assert "Date.now()" in countdown.group(1), "每個 tick 要用牆鐘重算剩餘"
        assert "left -= 1" not in countdown.group(1), (
            "tick 遞減會在背景分頁被節流後顯示假的剩餘秒數"
        )

    def test_expiry_keeps_polling_for_grace_window(self):
        js = _js()
        expired = re.search(r"renderExpired\(\)\s*\{(.*?)\n    \},", js, re.S)
        assert expired, "找不到 renderExpired——過期要有專屬狀態卡"
        body = expired.group(1)
        assert "_stopCountdown()" in body, "過期只停倒數，不停輪詢"
        assert "_clearTimers" not in body, (
            "過期就清掉輪詢＝最後一刻送出的綁定永遠偵測不到"
        )
        assert "_EXPIRY_POLL_GRACE_MS" in body, "輪詢寬限窗要有明確上限"
        assert "_EXPIRY_POLL_GRACE_MS: 60000" in js

    def test_expired_card_does_not_show_stale_token(self):
        expired = re.search(r"renderExpired\(\)\s*\{(.*?)\n    \},", _js(), re.S)
        assert expired
        assert "token" not in expired.group(1), (
            "過期卡不得再出現舊碼——那是「複製到過期碼」的陷阱來源"
        )
        assert "handleConnect" in expired.group(1), "過期卡要有重新產生按鈕"

    def test_loadStatus_preserves_live_instructions(self):
        js = _js()
        load_status = re.search(r"async loadStatus\(\)\s*\{(.*?)\n    \},", js, re.S)
        assert load_status, "找不到 loadStatus"
        guard = re.search(
            r"if \(this\._countdownTimer \|\| this\._pollTimer\)", load_status.group(1)
        )
        assert guard, (
            "說明卡活著時 loadStatus 只能檢查 bound，不得重渲染洗掉碼與輪詢"
        )
        # 守衛必須在 spinner 覆蓋 innerHTML 之前
        assert load_status.group(1).index("if (this._countdownTimer") < (
            load_status.group(1).index("innerHTML")
        )

    def test_new_i18n_keys_in_every_locale(self):
        for lang in LOCALES:
            data = json.loads(
                (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            for key in ("codeExpired", "regenerate"):
                assert key in data.get("line", {}), f"{lang}.json 缺 line.{key}"


def test_chat_language_follows_account_preference():
    """回覆語言跟帳號走（users.language；網頁切語即寫入）——「一個帳號、
    三個入口、一份偏好」（2026-09-09 DANNY 拍板）。修復前：LINE 寫死
    zh-TW、Telegram 只看客戶端 language_code，網頁設英文的使用者照收繁中。

    2026-09-28 DANNY：對話改成先照這則訊息的語言（core/reply_language.py），
    看不出來才用帳號偏好；帳號偏好仍要讀，而且排在客戶端 language_code 前面。
    """
    line_src = LINE_ROUTER.read_text(encoding="utf-8")
    assert 'language="zh-TW"' not in line_src, (
        "LINE 回覆語言不得寫死——要讀 users.language"
    )
    assert re.search(r"get_user_language,\s*binding\[\"user_id\"\]", line_src), (
        "LINE 要以綁定帳號的語言偏好解析回覆語言"
    )

    tg_src = TG_CHAT.read_text(encoding="utf-8")
    assert re.search(r"get_user_language,\s*binding\[\"user_id\"\]", tg_src), (
        "Telegram 要讀綁定帳號的語言偏好"
    )
    assert re.search(
        r"resolve_reply_language\(\s*message,\s*account_language,\s*default=language", tg_src
    ), (
        "Telegram：訊息語言 → 帳號偏好 → 客戶端 language_code"
    )
    assert "resolve_reply_language(" in line_src, "LINE：訊息語言 → 帳號偏好 → 繁中"

    exports = (REPO / "core" / "database" / "__init__.py").read_text(encoding="utf-8")
    assert '"get_user_language"' in exports, "core.database 未匯出 get_user_language"
