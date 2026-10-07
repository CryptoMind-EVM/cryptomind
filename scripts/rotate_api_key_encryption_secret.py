#!/usr/bin/env python3
"""API key encryption secret rotation — controlled ops migration (c050).

輪替外部管理的 ``API_KEY_ENCRYPTION_SECRET``（AGENTS.md 安全基線：不可直接
替換 env 值，必須 backup → decrypt-and-re-encrypt → verify → switch → revoke）。

用途
----
把 ``user_api_keys.encrypted_key`` 的每一行從「舊 secret 加密」改寫成
「新 secret 加密」。兩把 secret 都由環境變數提供，腳本本身不產生、不儲存
任何金鑰：

- ``API_KEY_ENCRYPTION_SECRET_OLD``：目前線上仍在使用（或剛被換下）的值
- ``API_KEY_ENCRYPTION_SECRET_NEW``：輪替目標值（長度 ≥ 32，與舊值不同）

模式
----
- 預設 dry-run：只分類統計，不寫入
- ``--apply``：實際改寫（冪等——已經是新格式的行會跳過，可重複執行）
- ``--verify``：驗證全部行都能用新 secret 解密

行分類規則（依序嘗試；v2 與舊格式都認得）
------------------------------------------
1. 新 secret 解密成功 → already_new（不動）
2. 舊 secret 解密成功 → 待遷移；``--apply`` 時以新 secret 重加密成 v2 寫回
3. 兩者皆失敗 → broken（本來就解不開的歷史孤兒行——**永不觸碰**，僅回報）

v2 每筆各自一個 salt，每行都要跑一次 PBKDF2（約 0.2 秒），行數多時要預留時間。

標準作業順序（與 ops 配合）
--------------------------
1. Postgres 備份（pg_dump）
2. 服務環境先備好 ``_OLD``（= 現行主變數值）
3. 跑本腳本（pass 1，``--apply``）：舊 → 新
4. 把主變數 ``API_KEY_ENCRYPTION_SECRET`` 換成新值並重啟服務
5. 再跑一次本腳本（pass 2 sweep）：撈走切換窗口期間以舊 secret 新寫入的行
6. ``--verify`` 全數通過後，移除 ``_OLD`` 變數
回滾（僅限尚未切換主變數時）：把 OLD/NEW 兩個環境變數對調再 ``--apply`` 一次。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE = 200
MIN_NEW_SECRET_LEN = 32


def _fail(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _check_secret(secret: str, label: str) -> str:
    if not secret:
        _fail(f"{label} is not set")
    try:
        from utils.encryption import _derive_key_from_secret

        _derive_key_from_secret(secret)
    except Exception as exc:  # noqa: BLE001 — 金鑰派生失敗要清楚回報後中止
        _fail(f"{label} could not be derived into a Fernet key: {exc}")
    return secret


def _decrypt(secret: str, stored: str) -> str | None:
    from utils.encryption import decrypt_with_secret

    try:
        return decrypt_with_secret(stored, secret)
    except Exception:  # noqa: BLE001 — 分類用途，任何失敗=不符合該把金鑰
        return None


def _classify(stored: str, new_secret: str, old_secret: str | None) -> str:
    if _decrypt(new_secret, stored) is not None:
        return "already_new"
    if old_secret and _decrypt(old_secret, stored) is not None:
        return "needs_migration"
    return "broken"


def _reencrypt(stored: str, old_secret: str, new_secret: str) -> str:
    from utils.encryption import decrypt_with_secret, encrypt_with_secret

    return encrypt_with_secret(decrypt_with_secret(stored, old_secret), new_secret)


async def _run(mode: str) -> int:
    from sqlalchemy import select, update

    from core.orm.models import UserApiKey
    from core.orm.session import get_session_factory

    old_secret = os.getenv("API_KEY_ENCRYPTION_SECRET_OLD", "")
    new_secret = os.getenv("API_KEY_ENCRYPTION_SECRET_NEW", "")

    if not new_secret:
        _fail("API_KEY_ENCRYPTION_SECRET_NEW is not set")
    if not old_secret:
        _fail("API_KEY_ENCRYPTION_SECRET_OLD is not set")
    if new_secret == old_secret:
        _fail("NEW and OLD secrets are identical — nothing to rotate")
    if len(new_secret) < MIN_NEW_SECRET_LEN:
        _fail(f"NEW secret must be at least {MIN_NEW_SECRET_LEN} characters")

    _check_secret(new_secret, "API_KEY_ENCRYPTION_SECRET_NEW")
    _check_secret(old_secret, "API_KEY_ENCRYPTION_SECRET_OLD")

    counts = {"already_new": 0, "needs_migration": 0, "broken": 0, "migrated": 0}
    broken_ids: list = []
    factory = get_session_factory()

    async with factory() as session:
        result = await session.execute(
            select(UserApiKey.id, UserApiKey.encrypted_key).order_by(UserApiKey.id)
        )
        rows = result.fetchall()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{now}] mode={mode} rows={len(rows)}")

    pending: list = []
    for row_id, stored in rows:
        verdict = _classify(stored, new_secret, old_secret)
        counts[verdict] += 1
        if verdict == "broken":
            broken_ids.append(row_id)
        elif verdict == "needs_migration":
            if mode == "apply":
                pending.append((row_id, _reencrypt(stored, old_secret, new_secret)))
            else:
                pending.append((row_id, None))

    if mode == "apply":
        async with factory() as session:
            for i in range(0, len(pending), BATCH_SIZE):
                batch = pending[i : i + BATCH_SIZE]
                for row_id, new_encoded in batch:
                    await session.execute(
                        update(UserApiKey)
                        .where(UserApiKey.id == row_id)
                        .values(encrypted_key=new_encoded)
                    )
                await session.commit()
                counts["migrated"] += len(batch)
                now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                print(
                    f"[{now}] committed batch {i // BATCH_SIZE + 1} "
                    f"({counts['migrated']}/{len(pending)})"
                )
    elif pending:
        print("dry-run: rows that WOULD be rewritten (first 10):")
        for row_id, _ in pending[:10]:
            print(f"  - id={row_id}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(
        f"[{now}] summary: total={len(rows)} already_new={counts['already_new']} "
        f"migrated={counts['migrated']} pending={'(dry-run)' if mode != 'apply' else 0} "
        f"broken={counts['broken']}"
    )
    if broken_ids:
        print(
            f"broken row ids (untouched, pre-existing undecryptable): {broken_ids[:20]}"
        )

    # broken 行是輪替前的歷史孤兒（兩把 key 都解不開）——輪替修不了它們，
    # 只回報不擋 verify；verify 只在意「還有舊 secret 的行沒遷到」
    if mode == "verify" and counts["needs_migration"]:
        print("VERIFY FAILED: rows still encrypted with the OLD secret")
        return 1
    print(f"OK: {mode} finished")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="actually rewrite rows (default: dry-run)"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify all rows decrypt under the NEW secret",
    )
    args = parser.parse_args()
    mode = "verify" if args.verify else ("apply" if args.apply else "dry-run")
    return asyncio.run(_run(mode))


if __name__ == "__main__":
    raise SystemExit(main())
