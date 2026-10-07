"""登入鏈徹查（2026-09-10 DANNY「徹底檢查並排除任何 BUG」）後端修復釘子。

對應三份獨立 code review 的後端發現：
- C-1  deps：過期/無效 token 回 401 而非 500（auto-refresh 只認 401）
- H-1  nonce 用後即焚（重放窗口 10 分鐘 → 第二次使用即 401）
- H-2  並發首登 UniqueViolation：回滾重查而非 500
- M-2  鎖定計數口徑：nonce 過期/格式錯不計暴力嘗試
- M-3  refresh：is_active 檢查＋舊 refresh token 輪換作廢
- L-2  wc-connect-event summary 換行消毒
"""

import re
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import api.evm_verification as ev

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestNonceBurn:
    """H-1：nonce 用後即焚。"""

    def test_replay_rejected(self):
        ev._used_nonces.clear()
        p = ev.generate_evm_nonce_payload()
        ev._consume_nonce(p)
        with pytest.raises(HTTPException) as exc:
            ev._consume_nonce(p)
        assert exc.value.status_code == 401
        assert "already used" in str(exc.value.detail)

    def test_different_nonces_both_pass(self):
        ev._used_nonces.clear()
        ev._consume_nonce(ev.generate_evm_nonce_payload())
        ev._consume_nonce(ev.generate_evm_nonce_payload())

    def test_expired_burn_entries_do_not_block_forever(self):
        ev._used_nonces.clear()
        p = ev.generate_evm_nonce_payload()
        ev._consume_nonce(p)
        # 模擬時間過了 TTL：條目過期後同一 payload 不再擋（真實 TTL 由
        # _check_payload 先擋，這裡只驗記憶體集合本身會自我清理）
        ev._used_nonces[next(iter(ev._used_nonces))] = time.time() - 1
        ev._consume_nonce(p)

    def test_burn_wired_into_both_verify_paths(self):
        src = _read("api/evm_verification.py")
        classic = re.search(r"def _prepare_siwe_recovery.*?(?=\ndef )", src, re.S)
        assert "_consume_nonce(payload)" in classic.group(0), "classic 路徑要燒 nonce"
        oneclick = re.search(
            r"def _prepare_wallet_message.*?(?=\n(?:async )?def )", src, re.S
        )
        assert '_consume_nonce(nonce_match.group("v"))' in oneclick.group(0), (
            "One-Click 路徑的信任錨同樣要燒"
        )

    def test_mint_path_does_not_burn(self):
        """鑄造端點自己呼叫 _check_payload——burn 不得塞在 _check_payload 裡。"""
        src = _read("api/evm_verification.py")
        check = re.search(r"def _check_payload.*?(?=\n\ndef |\n# ---)", src, re.S)
        assert "_consume_nonce" not in check.group(0), (
            "鑄造路徑共用 _check_payload，塞進去會把剛發的 nonce 燒掉"
        )


class TestDepsToken401:
    """C-1：PyJWT 例外必須轉 401。"""

    def test_expired_and_invalid_converted_to_401(self):
        src = _read("api/deps.py")
        block = re.search(
            r"except ExpiredSignatureError:.*?except HTTPException:", src, re.S
        )
        assert block, "找不到 token 解碼例外區塊"
        body = block.group(0)
        assert body.count("HTTP_401_UNAUTHORIZED") >= 1, (
            "ExpiredSignatureError / InvalidTokenError 都要轉 401，"
            "直接 re-raise 會穿透成 500、打斷前端 auto-refresh"
        )
        # 不得再出現裸的 raise（轉換前是 `if not TEST_MODE: raise`）
        assert not re.search(r"if not TEST_MODE:\s*\n\s*raise\s*\n", body), (
            "不得裸 re-raise PyJWT 例外"
        )


class TestConcurrentFirstLogin:
    """H-2：並發首登撞 PK 要重查而非 500。"""

    def test_unique_violation_reselects(self):
        src = _read("core/database/user.py")
        block = re.search(
            r"INSERT INTO users \(user_id, username, auth_method, created_at\).*?is_new\": False",
            src,
            re.S,
        )
        assert block, "找不到 INSERT 區塊"
        assert "UniqueViolation" in block.group(0), "要 catch UniqueViolation"
        assert block.group(0).count("SELECT user_id, username, auth_method") >= 1, (
            "撞 PK 後要重查既有用戶"
        )


class TestLockoutCountingScope:
    """M-2：nonce 過期/格式錯不計暴力嘗試。"""

    def test_proof_failures_narrowed(self):
        src = _read("api/routers/user.py")
        for tag in ("evm siwe invalid", "evm bind invalid"):
            m = re.search(
                r"except HTTPException as proof_err:\s*\n(.*?)\n\s*raise", src, re.S
            )
            assert m, f"找不到 proof 驗證 except 區塊（{tag}）"
            assert '"expired" not in' in m.group(1), "nonce 過期不得計入鎖定計數"


class TestRefreshRotation:
    """M-3：refresh 要查 is_active＋廢舊 token。"""

    def test_refresh_checks_active_and_revokes_old(self):
        src = _read("api/routers/user.py")
        refresh = re.search(
            r'@router\.post\("/api/user/refresh"\).*?(?=@router\.post)', src, re.S
        )
        assert refresh, "找不到 refresh 端點"
        body = refresh.group(0)
        assert "user_repo.get_by_id" in body, "refresh 要檢查用戶存在與 is_active"
        assert "revoke_token" in body, "refresh 成功後要作廢舊 refresh token"


class TestLogSanitize:
    """L-2：匿名遙測不得能偽造多行 log。"""

    def test_wc_event_summary_sanitized(self):
        src = _read("api/routers/user.py")
        block = re.search(
            r'def wc_connect_event.*?return \{"success": True\}', src, re.S
        )
        assert block, "找不到 wc_connect_event"
        assert 'replace("\\r", " ")' in block.group(0)
        assert 'replace("\\n", " ")' in block.group(0)
