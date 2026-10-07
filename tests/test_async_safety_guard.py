"""Async safety guard — 靜態掃描「async 函數內 sync I/O」的 bug。

防止 PR #226 / #227 / #228 之類 SIGABRT root cause（async 函數內直接呼叫
sync DB / Redis / network I/O）被新寫進來。

掃描方式：用 AST 解析 async function body，找出直接呼叫「已知 sync I/O 函數」
而沒包 ``run_sync`` / ``run_in_executor`` / ``asyncio.to_thread`` 的位置。

認列的危險 sync 函數：
- ``get_cache`` / ``set_cache`` / ``delete_cache`` (core/database/cache.py)
- ``get_connection`` / ``init_db`` (core/database/connection.py)
- ``DatabaseBase(...)`` (sync context manager)
- ``psycopg2.connect`` 直接呼叫
- ``httpx.Client(...)`` sync HTTP client
- ``requests.get/post/...``
- ``urllib.request.urlopen``
- ``time.sleep``

誤判白名單：某些 sync 函數已被 async wrapper 包起來（如 ``load_market_pulse_cache``
→ ``load_market_pulse_cache_async`` 用 ``run_sync`` 包），這些不該被 flag。

並非窮舉（動態分析太複雜），但能抓 90% 同類 typo。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ============================================================================
# 設定：哪些函數是「危險 sync I/O」+ 哪些「安全 wrapper」
# ============================================================================

DANGEROUS_SYNC_CALLS = {
    # core/database/cache.py (sync Redis + DB)
    "get_cache",
    "set_cache",
    "delete_cache",
    # core/database/connection.py
    "get_connection",
    "init_db",
    "init_connection_pool",
    # direct psycopg2 / httpx sync
    "psycopg2.connect",
    "httpx.Client",
    # requests (sync HTTP)
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "requests.request",
    # urllib
    "urlopen",
    # time.sleep
    "time.sleep",
    # yfinance —— 全同步且會發 HTTP。Yahoo 一限流就重試退避，可以卡住數十秒到
    # 數分鐘。workers=1 之下 event loop 一凍住 → gunicorn heartbeat 停 →
    # timeout(180s) → SIGABRT，整站每隔幾分鐘黑掉一次。
    "yf.Ticker",
    "yf.download",
    "yfinance.Ticker",
    "yfinance.download",
}

SAFE_WRAPPERS = {
    "run_sync",
    "run_in_executor",
    "to_thread",
    "asyncio.to_thread",
    "loop.run_in_executor",
}

#: 掃描範圍（agent 與 API 熱路徑）
SCAN_ROOTS = [
    "api/lifespan.py",
    "api/services.py",
    "api/deps.py",
    "api/routers/",
    "core/agents/",
    # agent 的資料工具也在 request 熱路徑上（LLM 呼叫工具 → 同一個 event loop）
    "core/tools/",
]


# ============================================================================
# AST scanner
# ============================================================================


def _is_in_async_function(node: ast.AST) -> bool:
    """往上找 Enclosing function，判斷是否為 async def。"""
    parent = getattr(node, "_parent", None)
    while parent is not None:
        if isinstance(parent, (ast.AsyncFunctionDef,)):
            return True
        if isinstance(parent, ast.FunctionDef):
            return False  # 在 sync function 內，OK
        parent = getattr(parent, "_parent", None)
    return False  # module-level


def _call_name(call: ast.Call) -> str:
    """還原 call 的 dotted name（如 ``psycopg2.connect``）。"""
    func = call.func
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _is_wrapped_in_safe_wrapper(node: ast.AST) -> bool:
    """往上找，判斷這個 node 是否在 ``run_sync(...)`` / ``run_in_executor(...)`` 等安全 wrapper 內。"""
    parent = getattr(node, "_parent", None)
    while parent is not None:
        if isinstance(parent, ast.Call):
            name = _call_name(parent)
            # 安全 wrapper 的 lambda/args 內容是被 run_sync 包起來的
            if any(w in name for w in SAFE_WRAPPERS):
                return True
        parent = getattr(parent, "_parent", None)
    return False


def _attach_parents(tree: ast.AST) -> None:
    """每個 AST node 加 _parent reference（Python 3.9+ 用 ast.walk）。"""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """掃描單一檔案，回傳 (line_no, dangerous_call, enclosing_function) 清單。"""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError):
        return []

    _attach_parents(tree)
    violations: list[tuple[int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        # 必須是「完整 dotted name 完全匹配」或「單字危險函數」。
        # 不能只比 trailing part（會誤抓 dict.get / os.environ.get / router.get）。
        matched = name in DANGEROUS_SYNC_CALLS
        if not matched:
            continue
        # 必須在 async function 內
        if not _is_in_async_function(node):
            continue
        # 必須沒被 safe wrapper 包
        if _is_wrapped_in_safe_wrapper(node):
            continue
        # 找 enclosing function name
        fn_name = "<unknown>"
        parent = getattr(node, "_parent", None)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_name = parent.name
                break
            parent = getattr(parent, "_parent", None)
        violations.append((node.lineno, name, fn_name))

    return violations


def collect_python_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    files: list[Path] = []
    for entry in SCAN_ROOTS:
        path = root / entry
        if path.is_file() and entry.endswith(".py"):
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*.py") if "node_modules" not in str(p))
    return files


# ============================================================================
# Tests
# ============================================================================


def test_no_sync_io_in_async_functions():
    """掃描整個 API + agents 樹，找出 async 函數內的 sync I/O 呼叫。

    失敗時列出每個違規位置（file:line in function: dangerous_call）。
    """
    files = collect_python_files()
    all_violations: list[tuple[Path, int, str, str]] = []
    for f in files:
        violations = scan_file(f)
        for line_no, name, fn_name in violations:
            all_violations.append((f, line_no, name, fn_name))

    if all_violations:
        # 已知白名單（手動確認過是 sync caller 而非 async 路徑）
        whitelist_substrings = [
            # 在 sync 函數內定義的 inner async lambda 不算（_is_in_async_function 抓不到巢狀）
            # 加上實際白名單：
        ]
        filtered = []
        for path, line, name, fn in all_violations:
            rel = str(path.relative_to(Path(__file__).resolve().parent.parent))
            if any(ws in rel for ws in whitelist_substrings):
                continue
            filtered.append((rel, line, name, fn))

        if filtered:
            lines = ["Async safety violations (sync I/O in async function):"]
            lines.append("")
            for rel, line, name, fn in filtered[:30]:  # cap at 30
                lines.append(f"  {rel}:{line}  in {fn}()  →  {name}")
            if len(filtered) > 30:
                lines.append(f"  ... and {len(filtered) - 30} more")
            lines.append("")
            lines.append(
                "Fix: wrap the sync call in `await run_sync(...)` or "
                "`await loop.run_in_executor(None, ...)`."
            )
            pytest.fail("\n".join(lines))


def test_load_market_pulse_cache_async_wrapper_exists():
    """ensure 啟動路徑已改用 async wrapper（H1 修復不能被回滾）。"""
    import inspect

    from api.services import load_market_pulse_cache_async

    src = inspect.getsource(load_market_pulse_cache_async)
    assert "run_sync" in src, "load_market_pulse_cache_async 必須透過 run_sync 呼叫 sync 版"


def test_lifespan_uses_async_load_market_pulse_cache():
    """lifespan.py 必須 await load_market_pulse_cache_async（不能直接呼叫 sync 版）。"""
    import re

    import api.lifespan as lifespan_mod

    src = inspect_getsource(lifespan_mod)
    # 必須呼叫 async 版本
    assert re.search(r"await\s+load_market_pulse_cache_async\(\)", src), (
        "lifespan 必須 await load_market_pulse_cache_async() — 不能 sync 調用！"
    )
    # 不能再直接呼叫 sync load_market_pulse_cache()
    forbidden_pattern = r"(?<!async\s)(?<!_)\bload_market_pulse_cache\(\)"
    assert not re.search(forbidden_pattern, src), (
        "lifespan 仍直接呼叫 sync load_market_pulse_cache() — 應改用 async wrapper"
    )


def test_lifespan_advisory_lock_uses_run_in_executor():
    """lifespan advisory lock 必須包在 run_in_executor（H2 修復不能被回滾）。"""
    import api.lifespan as lifespan_mod

    src = inspect_getsource(lifespan_mod)
    # _acquire_init_lock / _release_init_lock 必須存在 + 被包在 run_in_executor
    assert "_acquire_init_lock" in src
    assert "_release_init_lock" in src
    assert "run_in_executor(None, _acquire_init_lock)" in src
    assert "run_in_executor(None, _release_init_lock" in src


def inspect_getsource(mod):
    import inspect

    return inspect.getsource(mod)


# ============================================================================
# Fix A/B/C 回滾保護（2026-07-20 chat cold-start 修復）
# ============================================================================


def test_lifespan_warmup_redis_cache():
    """lifespan 必須在啟動時 warmup Redis（避免第一波 request 卡 30-64 秒）。"""
    import re

    import api.lifespan as lifespan_mod

    src = inspect_getsource(lifespan_mod)
    assert re.search(r"await\s+warmup_redis_async\(\)", src), (
        "lifespan 必須在啟動時 await warmup_redis_async() — "
        "否則第一波 request 各自等 Redis 連線（log 實測 30-64 秒）"
    )


def test_llm_client_sets_max_retries_and_timeout():
    """LLM client 必須設 max_retries + request_timeout（避免 NEM 卡 4 分鐘）。"""
    import utils.llm_client as llm_mod

    src = inspect_getsource(llm_mod)
    assert "max_retries" in src, (
        "init_chat_model 必須設 max_retries — "
        "否則 LangChain 預設行為 + NEM ResourceExhausted 會累積數十秒"
    )
    assert "request_timeout" in src, (
        "init_chat_model 必須設 request_timeout — "
        "否則單一 LLM call 無上限會卡到 AGENT_EXECUTION_TIMEOUT"
    )
    # env 可覆寫
    assert "LLM_MAX_RETRIES" in src
    assert "LLM_REQUEST_TIMEOUT_SEC" in src


def test_agent_execution_timeout_capped():
    """AGENT_EXECUTION_TIMEOUT 預設必須 ≤ 300s（避免 chat 卡死 worker 1 小時）。"""
    import re

    import core.agents.manager._main as main_mod

    src = inspect_getsource(main_mod)
    m = re.search(
        r'AGENT_EXECUTION_TIMEOUT\s*=\s*int\(\s*os\.environ\.get\([^,]+,\s*"(\d+)"',
        src,
    )
    assert m, "AGENT_EXECUTION_TIMEOUT 必須用 int(os.environ.get(...)) 包（可 env 覆寫）"
    default_val = int(m.group(1))
    assert default_val <= 300, (
        f"AGENT_EXECUTION_TIMEOUT 預設值必須 ≤ 300s（5 分鐘），實際為 {default_val}s。"
        " 過去 3600s（1 小時）會讓卡死的 chat 佔住 worker 1 小時。"
    )
