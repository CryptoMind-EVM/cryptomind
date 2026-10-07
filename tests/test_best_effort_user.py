"""best-effort user 識別測試 — api/deps.py + rate_limit.py。

驗證 PR B 修法：middleware 在 DI 之前能 best-effort 解 JWT 識別 user。
- best_effort_request_user：cookie/Bearer 解 JWT 拿 user_id
- get_user_identifier：有 user → per-user 限流；無 → per-IP fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock

from api.deps import best_effort_request_user
from api.middleware.rate_limit import get_user_identifier


def _make_request(cookie_token=None, bearer=None, state_user=None):
    """建一個 mock Request，可設 cookie / Bearer / state.user。"""
    req = MagicMock()
    req.headers = {}
    if bearer:
        req.headers["Authorization"] = f"Bearer {bearer}"
    req.cookies = {}
    if cookie_token:
        req.cookies["access_token"] = cookie_token
    # request.state 是 SimpleNamespace 風格
    state = MagicMock()
    state.user = state_user
    req.state = state
    return req


# ──────────────────────────────────────────────────────────────────────────────
# best_effort_request_user
# ──────────────────────────────────────────────────────────────────────────────


def test_best_effort_no_token_returns_none():
    """沒 cookie 也沒 Bearer → None。"""
    req = _make_request()
    assert best_effort_request_user(req) is None


def test_best_effort_invalid_token_returns_none():
    """無效 token → None（不 raise）。"""
    req = _make_request(cookie_token="invalid.jwt.token")
    assert best_effort_request_user(req) is None


def test_best_effort_valid_cookie_token_returns_user(monkeypatch):
    """有效 cookie JWT → 回 user dict。"""
    from api import deps

    # mock jwt.decode 回固定 payload
    monkeypatch.setattr(
        deps,
        "jwt",
        MagicMock(decode=MagicMock(return_value={"sub": "user-123", "username": "alice"})),
    )
    req = _make_request(cookie_token="valid.jwt.token")
    user = best_effort_request_user(req)
    assert user == {"user_id": "user-123", "username": "alice"}


def test_best_effort_bearer_token_returns_user(monkeypatch):
    """Bearer header 的 JWT 也能解。"""
    from api import deps

    monkeypatch.setattr(
        deps,
        "jwt",
        MagicMock(decode=MagicMock(return_value={"sub": "u9", "username": "bob"})),
    )
    req = _make_request(bearer="valid.jwt.token")
    user = best_effort_request_user(req)
    assert user["user_id"] == "u9"


def test_best_effort_cookie_preferred_over_bearer(monkeypatch):
    """cookie 與 bearer 同時存在時，cookie 優先（resolve_request_token 行為）。"""
    from api import deps

    decoded = {"sub": "cookie-user"}

    def fake_decode(token, secret, algorithms):
        # 確認解到的是 cookie 那個
        assert token == "cookie-token"
        return decoded

    monkeypatch.setattr(deps, "jwt", MagicMock(decode=fake_decode))
    req = _make_request(cookie_token="cookie-token", bearer="bearer-token")
    user = best_effort_request_user(req)
    assert user["user_id"] == "cookie-user"


def test_best_effort_token_without_sub_returns_none(monkeypatch):
    """token 有效但沒 sub → None。"""
    from api import deps

    monkeypatch.setattr(
        deps, "jwt", MagicMock(decode=MagicMock(return_value={"foo": "bar"}))
    )
    req = _make_request(cookie_token="valid.but.no.sub")
    assert best_effort_request_user(req) is None


# ──────────────────────────────────────────────────────────────────────────────
# get_user_identifier（per-user vs per-IP）
# ──────────────────────────────────────────────────────────────────────────────


def test_get_user_identifier_state_user():
    """request.state.user 已設 → 用 user。"""
    req = _make_request(state_user={"user_id": "u1"})
    assert get_user_identifier(req) == "user:u1"


def test_get_user_identifier_falls_back_to_best_effort(monkeypatch):
    """state.user 沒設 → best-effort 解 JWT。"""
    from api import deps

    monkeypatch.setattr(
        deps,
        "jwt",
        MagicMock(decode=MagicMock(return_value={"sub": "from-jwt"})),
    )
    req = _make_request(cookie_token="valid.jwt")
    assert get_user_identifier(req) == "user:from-jwt"


def test_get_user_identifier_falls_back_to_ip(monkeypatch):
    """state.user 沒設、也沒有效 token → per-IP。"""
    from api import deps

    monkeypatch.setattr(
        deps, "jwt", MagicMock(decode=MagicMock(side_effect=Exception("bad")))
    )
    req = _make_request()
    # 應回 ip: 開頭（mock request 的 remote_address 可能 None，但不影響 prefix 判斷）
    ident = get_user_identifier(req)
    assert ident.startswith("ip:")
