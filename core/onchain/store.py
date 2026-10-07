"""鏈上同步狀態與綁定錢包的資料存取（同步 psycopg；API 端用 run_sync 包）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.database.base import DatabaseBase

from .addresses import is_ton_address


def list_wallets(user_id: str) -> List[Dict[str, str]]:
    """綁定錢包（user_wallets）＋ TON 登入身份（user_id 本身就是地址，沒有綁定列）。

    EVM 在前（主要錢包先），TON 在後——平台已是 EVM 優先。
    """
    rows = DatabaseBase.query_all(
        """SELECT chain, address, is_primary FROM user_wallets
           WHERE user_id = %s
           ORDER BY is_primary DESC, bound_at ASC""",
        (user_id,),
    )
    wallets = [
        {
            "chain": r["chain"],
            "address": r["address"],
            "is_primary": bool(r["is_primary"]),
        }
        for r in rows
        if r.get("address")
    ]
    if is_ton_address(user_id) and not any(w["chain"] == "ton" for w in wallets):
        wallets.append({"chain": "ton", "address": user_id, "is_primary": not wallets})
    order = {"evm": 0, "ton": 1}
    return sorted(
        wallets, key=lambda w: (order.get(w["chain"], 9), not w["is_primary"])
    )


def user_language(user_id: str) -> str:
    row = DatabaseBase.query_one(
        "SELECT language FROM users WHERE user_id = %s", (user_id,)
    )
    return str((row or {}).get("language") or "zh-TW")


def get_status(user_id: str) -> Dict[str, Any]:
    row = DatabaseBase.query_one(
        "SELECT enabled, last_synced_at, last_result FROM user_onchain_sync WHERE user_id = %s",
        (user_id,),
    )
    if not row:
        return {
            "enabled": True,
            "last_synced_at": None,
            "last_result": None,
            "has_row": False,
        }
    result = row.get("last_result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    return {
        "enabled": bool(row.get("enabled")),
        "last_synced_at": row.get("last_synced_at"),
        "last_result": result,
        "has_row": True,
    }


def set_enabled(user_id: str, enabled: bool) -> None:
    DatabaseBase.execute(
        """INSERT INTO user_onchain_sync (user_id, enabled, updated_at)
           VALUES (%s, %s, NOW())
           ON CONFLICT (user_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW()""",
        (user_id, bool(enabled)),
    )


def record_result(
    user_id: str, result: Dict[str, Any], *, at: Optional[datetime] = None
) -> None:
    DatabaseBase.execute(
        """INSERT INTO user_onchain_sync (user_id, last_synced_at, last_result, updated_at)
           VALUES (%s, %s, %s::jsonb, NOW())
           ON CONFLICT (user_id) DO UPDATE SET
             last_synced_at = EXCLUDED.last_synced_at,
             last_result = EXCLUDED.last_result,
             updated_at = NOW()""",
        (
            user_id,
            at or datetime.now(timezone.utc),
            json.dumps(result, ensure_ascii=False, default=str),
        ),
    )


def list_sync_candidates() -> List[str]:
    """cron 對象：有綁定錢包（或 TON 身份）且沒關掉同步的 user_id。

    TON 身份原本用 ``LIKE 'EQ%' OR LIKE 'UQ%'`` 認，raw（0:／-1:）、testnet（kQ／0Q）、
    masterchain（Ef／Uf…）寫法登入的人全被跳過。SQL 先粗篩地址形狀（friendly 48 字元／
    raw），再用 is_ton_address 解碼確認（同 cron_recompute_trust）；有綁錢包的不受影響。
    """
    rows = DatabaseBase.query_all(
        """SELECT u.user_id,
                  EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.user_id) AS has_wallet
           FROM users u
           LEFT JOIN user_onchain_sync s ON s.user_id = u.user_id
           WHERE u.is_active = TRUE
             AND COALESCE(s.enabled, TRUE) = TRUE
             AND (EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.user_id)
                  OR length(u.user_id) = 48
                  OR u.user_id ~ '^-?[0-9]+:[0-9a-fA-F]{64}$')"""
    )
    return [
        r["user_id"] for r in rows if r["has_wallet"] or is_ton_address(r["user_id"])
    ]
