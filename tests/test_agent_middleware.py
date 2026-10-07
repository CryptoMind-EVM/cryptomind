"""agent middleware（規劃能力）測試。

create_agent + TodoListMiddleware 的環境標誌控制。DEEP_AGENTS_PLANNING_ENABLED
控制規劃能力開關。預設關閉（行為等價純 ReAct），啟用時模型得到 write_todos 工具。

不用 create_deep_agent——它會注入 file/sub-agent 工具且難以乾淨關閉。
create_agent + 選擇性 middleware 達成規劃能力，行為完全可控。
"""
from __future__ import annotations

from core.agents.base_react_agent import _build_agent_middleware, _planning_enabled

# ============================================================================
# _planning_enabled — 環境標誌控制
# ============================================================================


def test_planning_disabled_by_default(monkeypatch):
    """預設關閉——行為等價純 ReAct。"""
    monkeypatch.delenv("DEEP_AGENTS_PLANNING_ENABLED", raising=False)
    assert _planning_enabled() is False


def test_planning_enabled_with_true(monkeypatch):
    """DEEP_AGENTS_PLANNING_ENABLED=true → 啟用。"""
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "true")
    assert _planning_enabled() is True


def test_planning_enabled_with_1(monkeypatch):
    """DEEP_AGENTS_PLANNING_ENABLED=1 → 啟用。"""
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "1")
    assert _planning_enabled() is True


def test_planning_disabled_with_false(monkeypatch):
    """DEEP_AGENTS_PLANNING_ENABLED=false → 關閉。"""
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "false")
    assert _planning_enabled() is False


def test_planning_disabled_with_empty(monkeypatch):
    """DEEP_AGENTS_PLANNING_ENABLED='' → 關閉。"""
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "")
    assert _planning_enabled() is False


# ============================================================================
# _build_agent_middleware — middleware 清單組裝
# ============================================================================


def test_middleware_empty_when_planning_disabled(monkeypatch):
    """規劃關閉 → middleware 空清單（行為等價純 ReAct）。"""
    monkeypatch.delenv("DEEP_AGENTS_PLANNING_ENABLED", raising=False)
    mw = _build_agent_middleware()
    assert mw == []


def test_middleware_has_todolist_when_planning_enabled(monkeypatch):
    """規劃啟用 → middleware 含 TodoListMiddleware。"""
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "true")
    mw = _build_agent_middleware()
    assert len(mw) == 1
    from langchain.agents.middleware import TodoListMiddleware

    assert isinstance(mw[0], TodoListMiddleware)


def test_middleware_no_file_or_subagent_tools(monkeypatch):
    """規劃啟用也不注入 file/sub-agent 工具（關鍵：不用 create_deep_agent）。

    這是選 create_agent + middleware 而非 create_deep_agent 的核心原因——
    後者會注入 ls/read_file/write_file/task 等 8 個工具且難以乾淨關閉。
    """
    monkeypatch.setenv("DEEP_AGENTS_PLANNING_ENABLED", "true")
    mw = _build_agent_middleware()
    # 確認只有 TodoListMiddleware，沒有 FilesystemMiddleware / SubAgentMiddleware
    from langchain.agents.middleware import TodoListMiddleware

    allowed_types = {TodoListMiddleware}
    for m in mw:
        assert type(m) in allowed_types, f"未預期的 middleware: {type(m).__name__}"
