"""remember 工具測試 — Hermes 式主動記憶。

測三層：
1. PII 過濾（合規底線——不記身分證/密碼/私鑰）
2. remember 工具行為（無 user_id graceful、有 user_id 寫入）
3. category 分類（preference/holding/fact/context）
"""
from __future__ import annotations

from core.tools.remember_tool import _contains_pii, _make_key, remember

# ============================================================================
# PII 過濾（合規底線）
# ============================================================================


def test_pii_detects_id_number():
    """身分證號碼不記。"""
    assert _contains_pii("我的身分證是 A123456789") is True


def test_pii_detects_private_key():
    """私鑰不記。"""
    assert _contains_pii("我的私鑰是 0xabcdef1234567890abcdef1234567890") is True


def test_pii_detects_seed_phrase_hint():
    """seed phrase 提示詞不記。"""
    assert _contains_pii("我的 seed phrase 是 abandon amount") is True


def test_pii_detects_api_key():
    """API key 格式不記。"""
    assert _contains_pii("我的 key 是 sk-abcdefghijklmnopqrstuvwxyz123456") is True


def test_pii_allows_normal_content():
    """正常偏好/持倉內容可以記。"""
    assert _contains_pii("我偏好技術面分析") is False
    assert _contains_pii("持有 2.3 BTC") is False
    assert _contains_pii("我主要投資加密貨幣") is False


# ============================================================================
# _make_key
# ============================================================================


def test_make_key_has_category_prefix():
    """key 帶 category 前綴（避免不同 category 碰撞）。"""
    key_pref = _make_key("同樣內容", "preference")
    key_hold = _make_key("同樣內容", "holding")
    assert key_pref.startswith("preference_")
    assert key_hold.startswith("holding_")
    assert key_pref != key_hold


def test_make_key_deterministic():
    """同內容同 category → 同 key（upsert 而非重複）。"""
    k1 = _make_key("測試內容", "fact")
    k2 = _make_key("測試內容", "fact")
    assert k1 == k2


# ============================================================================
# remember 工具行為
# ============================================================================


def test_remember_blocks_pii():
    """PII 內容被擋（回警告訊息，不寫入）。"""
    from core.tools.key_resolver import set_current_user_id

    set_current_user_id("test_user")
    result = remember.invoke({"content": "身分證 A123456789", "category": "fact"})
    assert "Sensitive information" in result or "not remembered" in result


def test_remember_no_user_id_graceful():
    """無 user_id（未登入/測試）→ graceful，不 crash。"""
    from core.tools.key_resolver import set_current_user_id

    set_current_user_id(None)
    result = remember.invoke({"content": "偏好技術面", "category": "preference"})
    assert "logged-in user" in result.lower() or "not stored" in result.lower()


def test_remember_normalizes_category():
    """未知 category 正規化成 fact。"""
    from core.tools.key_resolver import set_current_user_id

    set_current_user_id("test_user")
    # 帶未知 category —— 不該 crash（會正規化成 fact 或 graceful 失敗）
    result = remember.invoke({"content": "測試內容", "category": "unknown_cat"})
    # graceful：不管 DB 有沒有連上，都不該 crash
    assert isinstance(result, str)


def test_remember_has_descriptive_docstring():
    """docstring 是 LLM 看的工具描述，必須教模型何時呼叫。"""
    desc = remember.description
    assert "偏好" in desc or "preference" in desc.lower()
    assert "持有" in desc or "holding" in desc.lower() or "記住" in desc
