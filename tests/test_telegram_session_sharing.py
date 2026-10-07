"""Telegram ↔ Web 聊天記錄共用 / session 切換的單元測試。

這些測試不需要 live DB —— 以 monkeypatch 取代 DB 存取，專注驗證
「切換 session 後以選中 session 的歷史為準」「/sessions 清單正確呈現」
「一次性 token nonce 抽取」等核心邏輯。
"""

import pytest

# ---------------------------------------------------------------------------
# 切換 session 後，歷史以「選中的 session」為準（telegram_chat._build_history_text）
# ---------------------------------------------------------------------------


def test_history_uses_selected_session(monkeypatch):
    from api.routers import telegram_chat as tc

    selected = [
        {"role": "user", "content": "BTC 怎麼看"},
        {"role": "assistant", "content": "短線偏多"},
    ]
    monkeypatch.setattr(
        tc, "get_chat_history", lambda session_id, limit: selected
    )

    out = tc._build_history_text("web-session-abc", "接下來呢")

    assert "用戶: BTC 怎麼看" in out
    assert "助手: 短線偏多" in out


def test_history_excludes_current_message(monkeypatch):
    """當前這句若已在歷史中（撈取時序競爭），不應重複帶入。"""
    from api.routers import telegram_chat as tc

    monkeypatch.setattr(
        tc,
        "get_chat_history",
        lambda session_id, limit: [{"role": "user", "content": "重複訊息"}],
    )

    out = tc._build_history_text("s", "重複訊息")

    assert out == ""


def test_history_empty_session(monkeypatch):
    from api.routers import telegram_chat as tc

    monkeypatch.setattr(tc, "get_chat_history", lambda session_id, limit: [])

    assert tc._build_history_text("s", "q") == ""


def test_history_load_error_is_swallowed(monkeypatch):
    """撈歷史失敗不應讓聊天崩潰，回空字串即可。"""
    from api.routers import telegram_chat as tc

    def boom(session_id, limit):
        raise RuntimeError("db down")

    monkeypatch.setattr(tc, "get_chat_history", boom)

    assert tc._build_history_text("s", "q") == ""


# ---------------------------------------------------------------------------
# /sessions 清單呈現（bot._build_sessions_keyboard）
# ---------------------------------------------------------------------------


def test_sessions_keyboard_lists_web_excludes_tg_marks_active():
    from bot import telegram_bot as b

    data = {
        "active_session_id": "abc",
        "sessions": [
            {"id": "tg:1", "title": "Telegram Chat", "is_active": False},
            {"id": "abc", "title": "BTC chat", "is_active": True},
            {"id": "def", "title": "ETH chat", "is_active": False},
        ],
    }
    kb = b._build_sessions_keyboard(data, "en")

    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]

    # 預設 tg session 不出現在 web 按鈕列（由獨立的「預設」按鈕呈現）
    assert "tgsess:tg:1" not in cbs
    # web sessions 都在
    assert "tgsess:abc" in cbs
    assert "tgsess:def" in cbs
    # 一定有「預設 Telegram 對話」按鈕
    assert "tgsess:" in cbs
    # 目前選中的 session 有 ✅ 標記
    active_btn = next(t for t in texts if "BTC chat" in t)
    assert active_btn.startswith("✅")


def test_sessions_keyboard_default_active_when_no_selection():
    from bot import telegram_bot as b

    data = {
        "active_session_id": "tg:9",  # 預設 session = 未選 web 對話
        "sessions": [{"id": "abc", "title": "BTC", "is_active": False}],
    }
    kb = b._build_sessions_keyboard(data, "en")
    default_btn = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == "tgsess:"
    )
    assert default_btn.text.startswith("✅")


def test_sessions_keyboard_truncates_long_title():
    from bot import telegram_bot as b

    long_title = "X" * 80
    data = {"active_session_id": None, "sessions": [{"id": "a", "title": long_title}]}
    kb = b._build_sessions_keyboard(data, "en")
    btn = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == "tgsess:a"
    )
    # label = 前綴 + 截斷標題 + 省略號，需短於原始長度
    assert "…" in btn.text
    assert len(btn.text) < len(long_title) + 4


# ---------------------------------------------------------------------------
# 一次性 link token：nonce 抽取（telegram_link._extract_token_nonce）
# ---------------------------------------------------------------------------


def test_extract_token_nonce_roundtrip():
    from api.routers import telegram_link as tl

    tok = tl.generate_link_token("EQ_wallet_addr")
    assert tl._extract_token_nonce(tok) == tok.split(".")[2]


@pytest.mark.parametrize("bad", ["", "garbage", "a.b.c", "only.three"])
def test_extract_token_nonce_malformed(bad):
    from api.routers import telegram_link as tl

    assert tl._extract_token_nonce(bad) is None
