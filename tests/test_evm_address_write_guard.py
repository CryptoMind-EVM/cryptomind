"""users.evm_address 只能在簽章驗過之後寫（#876 security review MEDIUM-1）。

get_verified_evm_address 把 users.evm_address 當成「簽章證明過的地址」（principal、
信任分數都信它），但這只靠呼叫慣例保證：set_user_evm_address 本身不驗簽。
這支用 AST 掃出每一個呼叫點（含把函式當參數傳出去，如 run_sync(set_user_evm_address, …)），
只允許兩條已驗簽的路徑：

- api/routers/user.py::_sync_trust_evm_binding——只被 evm_login 在 SIWE 驗章後呼叫
- api/routers/trust.py::bind_evm_address——/api/trust/evm/bind，先 verify_evm_signature

新增呼叫點會讓這裡紅，逼人確認那條路徑有沒有先驗簽。註解、字串、docstring
提到函式名都不算（AST 看不到註解，只數 Name／Attribute 參照）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("api", "core", "scripts", "bot", "analysis", "utils", "safety_kernel")
TARGET = "set_user_evm_address"
ALLOWED = {
    ("api/routers/user.py", "_sync_trust_evm_binding"),
    ("api/routers/trust.py", "bind_evm_address"),
}


def _references(tree: ast.AST, name: str) -> list[tuple[str, int]]:
    """(外層函式名, 行號)：每一個讀取 name 的地方（呼叫或當值傳出去）。"""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = child.name
            hit = (
                isinstance(child, ast.Name)
                and child.id == name
                and isinstance(child.ctx, ast.Load)
            ) or (
                isinstance(child, ast.Attribute)
                and child.attr == name
                and isinstance(child.ctx, ast.Load)
            )
            if hit:
                found.append((func, child.lineno))
            visit(child, scope)

    visit(tree, "<module>")
    return found


def _first_call_line(fn: ast.AST, names: set[str]) -> int | None:
    lines = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id in names)
            or (isinstance(n.func, ast.Attribute) and n.func.attr in names)
        )
    ]
    return min(lines) if lines else None


def _function(path: str, name: str) -> ast.AST:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _all_call_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for top in SCAN_DIRS:
        root = REPO / top
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO).as_posix()
            for func, _line in _references(tree, TARGET):
                sites.add((rel, func))
    return sites


def test_scanner_ignores_comments_and_strings_but_catches_indirect_calls():
    """守衛自己要可靠：註解／字串不能充數，當參數傳出去也要抓到。"""
    src = (
        "# _sync_trust_evm_binding: set_user_evm_address(user, addr)\n"
        "def fine():\n"
        '    """set_user_evm_address(user, addr)"""\n'
        '    log("set_user_evm_address")\n'
        "def sneaky():\n"
        "    run_sync(set_user_evm_address, user, addr)\n"
        "def direct():\n"
        "    db.set_user_evm_address(user, addr)\n"
    )
    funcs = {f for f, _ in _references(ast.parse(src), TARGET)}
    assert funcs == {"sneaky", "direct"}


def test_only_signature_verified_paths_write_users_evm_address():
    sites = _all_call_sites()
    unexpected = sites - ALLOWED
    assert not unexpected, (
        f"{TARGET} 有新的呼叫點：{sorted(unexpected)}。users.evm_address 會被當成"
        "簽章證明過的地址（principal、信任分數）——確認這條路徑先驗過 SIWE／"
        "personal_sign，再把它加進 ALLOWED。"
    )
    assert ALLOWED <= sites, f"已知呼叫點不見了（改名？）：{sorted(ALLOWED - sites)}"


def test_trust_bind_verifies_signature_before_writing():
    fn = _function("api/routers/trust.py", "bind_evm_address")
    verify = _first_call_line(fn, {"verify_evm_signature"})
    write = _first_call_line(fn, {TARGET})
    assert verify is not None and write is not None and verify < write


def test_login_autobind_only_runs_after_siwe_verification():
    """_sync_trust_evm_binding 只能從 evm_login 呼叫，且在 SIWE 驗章之後。"""
    tree = ast.parse((REPO / "api/routers/user.py").read_text(encoding="utf-8"))
    callers = {f for f, _ in _references(tree, "_sync_trust_evm_binding")}
    assert callers == {"evm_login"}
    fn = _function("api/routers/user.py", "evm_login")
    verify = _first_call_line(
        fn, {"verify_siwe_wallet_message_async", "recover_siwe_signer_async"}
    )
    autobind = min(line for f, line in _references(fn, "_sync_trust_evm_binding"))
    assert verify is not None and verify < autobind


def test_setter_documents_the_invariant():
    from core.database.user import set_user_evm_address

    doc = set_user_evm_address.__doc__ or ""
    assert "SIWE" in doc and "personal_sign" in doc
