"""BYOK 金鑰加密格式升級（v2：每筆隨機 salt）的讀取時遷移與輪替腳本相容。

- repo 讀到舊格式（無前綴、固定 salt）且解得開 → 同一個 session 內以
  compare-and-swap 改寫成 v2（WHERE encrypted_key = 讀到的舊值，避免蓋掉
  使用者同時存的新金鑰），updated_at 不動
- 已是 v2、或解不開的列 → 不寫（解不開的列絕不覆蓋）
- 改寫失敗不能讓讀取失敗
- scripts/rotate_api_key_encryption_secret.py 要認得 v2 列，否則輪替時會被
  當成 broken 跳過、切換 secret 後整批解不開
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from core.orm.user_api_keys_repo import user_api_keys_repo

from .test_encryption import (
    LEGACY_ENV_CIPHERTEXT,
    LEGACY_PLAINTEXT,
    LEGACY_SECRET,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _env_secret(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.encryption.KEYS_DIR", tmp_path)
    monkeypatch.setattr(
        "utils.encryption.KEYS_FILE", tmp_path / "api_key_encryption.json"
    )
    monkeypatch.setattr("utils.encryption._encryption_key_cache", None)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("API_KEY_ENCRYPTION_SECRET", LEGACY_SECRET)


class FakeResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, results=None, fail_updates=False):
        self.executed = []
        self._results = list(results or [])
        self._fail_updates = fail_updates

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt):
        if getattr(stmt, "is_update", False) and self._fail_updates:
            raise RuntimeError("simulated DB failure")
        self.executed.append(stmt)
        if getattr(stmt, "is_update", False):
            return FakeResult()
        return self._results.pop(0) if self._results else FakeResult()

    def updates(self):
        return [s for s in self.executed if getattr(s, "is_update", False)]


def _assert_cas_upgrade(stmt, legacy_value: str) -> str:
    from utils.encryption import decrypt_api_key

    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    params = compiled.params
    assert "UPDATE user_api_keys" in sql
    # compare-and-swap：只改寫「還是剛剛讀到的那個舊值」的列
    assert params["encrypted_key_1"] == legacy_value
    # 背景升級不是使用者動作，updated_at 保持原值
    assert "updated_at=user_api_keys.updated_at" in sql
    new_value = params["encrypted_key"]
    assert new_value.startswith("v2:")
    assert decrypt_api_key(new_value) == LEGACY_PLAINTEXT
    return new_value


@pytest.mark.unit
async def test_get_user_api_key_rewrites_legacy_row_as_v2():
    s = FakeSession(results=[FakeResult(scalar=LEGACY_ENV_CIPHERTEXT)])

    key = await user_api_keys_repo.get_user_api_key("u1", "openai", session=s)

    assert key == LEGACY_PLAINTEXT
    [upd] = s.updates()
    _assert_cas_upgrade(upd, LEGACY_ENV_CIPHERTEXT)


@pytest.mark.unit
async def test_get_user_api_key_with_model_rewrites_legacy_row():
    s = FakeSession(results=[FakeResult(rows=[(LEGACY_ENV_CIPHERTEXT, "gpt-x")])])

    info = await user_api_keys_repo.get_user_api_key_with_model(
        "u1", "openai", session=s
    )

    assert info == {"api_key": LEGACY_PLAINTEXT, "model": "gpt-x"}
    [upd] = s.updates()
    _assert_cas_upgrade(upd, LEGACY_ENV_CIPHERTEXT)


@pytest.mark.unit
async def test_get_user_api_key_masked_rewrites_legacy_row():
    s = FakeSession(results=[FakeResult(rows=[(LEGACY_ENV_CIPHERTEXT, "gpt-x", None)])])

    info = await user_api_keys_repo.get_user_api_key_masked("u1", "openai", session=s)

    assert info["has_key"] is True
    assert info["corrupted"] is False
    [upd] = s.updates()
    _assert_cas_upgrade(upd, LEGACY_ENV_CIPHERTEXT)


@pytest.mark.unit
async def test_get_all_user_api_keys_rewrites_legacy_rows():
    s = FakeSession(
        results=[
            FakeResult(rows=[("openai", LEGACY_ENV_CIPHERTEXT, "gpt-x", None)]),
            FakeResult(rows=[]),
        ]
    )

    keys = await user_api_keys_repo.get_all_user_api_keys("u1", kind="llm", session=s)

    assert keys["openai"]["has_key"] is True
    [upd] = s.updates()
    _assert_cas_upgrade(upd, LEGACY_ENV_CIPHERTEXT)


@pytest.mark.unit
async def test_v2_row_is_not_rewritten():
    from utils.encryption import encrypt_api_key

    stored = encrypt_api_key("sk-unit-test-already-v2")
    s = FakeSession(results=[FakeResult(scalar=stored)])

    key = await user_api_keys_repo.get_user_api_key("u1", "openai", session=s)

    assert key == "sk-unit-test-already-v2"
    assert s.updates() == []


@pytest.mark.unit
async def test_undecryptable_legacy_row_is_never_overwritten(monkeypatch):
    # 換一把 secret：舊列解不開 → 不能寫任何東西（留著等 secret 修好還救得回來）
    monkeypatch.setenv(
        "API_KEY_ENCRYPTION_SECRET", "some-other-unit-test-secret-xxxxxxxxxxx"
    )
    s = FakeSession(results=[FakeResult(scalar=LEGACY_ENV_CIPHERTEXT)])

    key = await user_api_keys_repo.get_user_api_key("u1", "openai", session=s)

    assert key is None
    assert s.updates() == []


@pytest.mark.unit
async def test_rewrite_failure_does_not_break_the_read():
    s = FakeSession(
        results=[FakeResult(rows=[(LEGACY_ENV_CIPHERTEXT, "gpt-x")])],
        fail_updates=True,
    )

    info = await user_api_keys_repo.get_user_api_key_with_model(
        "u1", "openai", session=s
    )

    assert info == {"api_key": LEGACY_PLAINTEXT, "model": "gpt-x"}


@pytest.mark.unit
async def test_save_always_writes_v2():
    s = FakeSession()

    result = await user_api_keys_repo.save_user_api_key(
        "u1", "tavily", "tvly-unit-test-key", session=s
    )

    assert result["success"] is True
    compiled = s.executed[0].compile(dialect=postgresql.dialect())
    assert compiled.params["encrypted_key"].startswith("v2:")


# ---------------------------------------------------------------------------
# scripts/rotate_api_key_encryption_secret.py
# ---------------------------------------------------------------------------

NEW_SECRET = "unit-test-only-byok-master-secret-NEW-0002"


def _load_rotation_script(monkeypatch):
    # 腳本 import 時會 load_dotenv()；worktree 往上找會撿到主目錄的 .env，測試不能吃真實設定
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    spec = importlib.util.spec_from_file_location(
        "rotate_api_key_encryption_secret",
        REPO / "scripts" / "rotate_api_key_encryption_secret.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_rotation_script_classifies_both_formats(monkeypatch):
    from utils.encryption import encrypt_with_secret

    script = _load_rotation_script(monkeypatch)
    v2_old = encrypt_with_secret("sk-unit-test-v2-old", LEGACY_SECRET)
    v2_new = encrypt_with_secret("sk-unit-test-v2-new", NEW_SECRET)

    assert script._classify(LEGACY_ENV_CIPHERTEXT, NEW_SECRET, LEGACY_SECRET) == (
        "needs_migration"
    )
    # v2 列以前會被歸成 broken → 永不遷移 → 切換 secret 後遺失
    assert script._classify(v2_old, NEW_SECRET, LEGACY_SECRET) == "needs_migration"
    assert script._classify(v2_new, NEW_SECRET, LEGACY_SECRET) == "already_new"
    assert script._classify("v2:AAAA", NEW_SECRET, LEGACY_SECRET) == "broken"


@pytest.mark.unit
def test_rotation_script_reencrypts_as_v2_under_new_secret(monkeypatch):
    from utils.encryption import decrypt_with_secret, encrypt_with_secret

    script = _load_rotation_script(monkeypatch)
    for stored, plain in [
        (LEGACY_ENV_CIPHERTEXT, LEGACY_PLAINTEXT),
        (
            encrypt_with_secret("sk-unit-test-v2-old", LEGACY_SECRET),
            "sk-unit-test-v2-old",
        ),
    ]:
        rewritten = script._reencrypt(stored, LEGACY_SECRET, NEW_SECRET)
        assert rewritten.startswith("v2:")
        assert decrypt_with_secret(rewritten, NEW_SECRET) == plain


# ---------------------------------------------------------------------------
# 前端 localStorage 降級加密（web/js/apiKeyManager.js）——node + webcrypto
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frontend_local_crypto_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "apikey_local_crypto.mjs")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    # 失敗時 node 會把整個 data: URL 模組原始碼印進 stack，只看尾巴
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "apikey_local_crypto: ok" in proc.stderr
