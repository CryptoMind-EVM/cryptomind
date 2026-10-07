"""
定時任務：錢包監測輪詢 + 警示分發

每 N 分鐘（WALLET_MONITOR_INTERVAL_MINUTES，預設 10）執行：
    1. 讀所有有警示設定的用戶（monitored_wallets 非空）
    2. entitlement gate（WALLET_MONITOR_PREMIUM_GATE_ENABLED）：只保留 active
       premium 用戶；free／過期 premium 跳過（design §9.7，fail-closed）
    3. 對每個監測錢包：抓 TonAPI events → 比對規則 → AlertEvent
    4. 分發到用戶啟用的 channel（站內 + Telegram）

設計：
    - 單一用戶失敗不中斷批次（per-user try/except）。
    - 去重內建於 engine（last_event_id 快取，按用戶分開）——不會重複警示。
    - 警示疲勞緩解：min_amount_ton 過濾 + 合併（v1 每事件一則，v2 合併）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from core.config import (
    WALLET_MONITOR_ENABLED,
    WALLET_MONITOR_EVM_ENABLED,
    WALLET_MONITOR_PREMIUM_GATE_ENABLED,
)
from core.database.connection import get_connection
from core.entitlement import resolve_entitlement_for_user
from core.wallet_monitor.channels import alert_dispatcher
from core.wallet_monitor.engine import fetch_new_events, match_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s [wallet-monitor] %(message)s")
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


def _entitlement_filter(
    users: list[tuple[str, dict]],
    *,
    gate_enabled: bool,
    resolver=resolve_entitlement_for_user,
) -> tuple[list[tuple[str, dict]], int]:
    """依 entitlement 過濾排程監測用戶（design 2026-08-13 §9.7）。

    gate 關閉 → 原樣回傳（既有行為，安全漸進上線）。
    gate 開啟 → 只保留 ``can_use_scheduled_monitoring`` 為 True 的用戶（active
    premium；過期 premium／free 一律跳過，fail-closed）。

    回傳 (kept_users, skipped_count)。skipped_count 供 policy B 遷移觀測用。
    resolver 預設為 ``resolve_entitlement_for_user``，可注入 fake 以利單元測試。
    """
    if not gate_enabled:
        return users, 0
    kept: list[tuple[str, dict]] = []
    skipped = 0
    for uid, settings in users:
        try:
            ent = resolver(uid)
        except Exception:  # noqa: BLE001
            # resolver 內部已 fail-safe 成 free；此處防禦性再保險。
            ent = None
        if ent is not None and ent.can_use_scheduled_monitoring:
            kept.append((uid, settings))
        else:
            skipped += 1
    return kept, skipped


def _fetch_users_with_monitors() -> list[tuple[str, dict]]:
    """讀所有有監測設定且 monitored_wallets 非空的用戶。

    空清單在 SQL 就濾掉（存過警示設定的人都有一列，錢包全移除後也還在，不該
    全撈回來）；下面的 Python 判斷保留，SQL 只拿掉它本來就會丟的列，結果不變。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT user_id, wallet_alert_settings
                FROM users
                WHERE wallet_alert_settings IS NOT NULL
                  AND is_active = TRUE
                  AND (
                      wallet_alert_settings->'monitored_wallets'
                          NOT IN ('null'::jsonb, '[]'::jsonb)
                      -- 被編碼成 JSON 字串的設定（-> 取不到）交給下面 json.loads 判斷
                      OR jsonb_typeof(wallet_alert_settings) = 'string'
                  )
                """
            )
            rows = c.fetchall()
        users = []
        for user_id, raw in rows:
            try:
                settings = raw if isinstance(raw, dict) else json.loads(raw)
            except (ValueError, TypeError):
                continue
            if settings.get("monitored_wallets"):
                users.append((user_id, settings))
        return users
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _wallet_entries(wallets: list) -> list[dict]:
    """把 monitored_wallets(純字串或 {address,label,reason,chain})統一成物件陣列。

    路線 C：保留 chain 資訊，不再壓平成純地址（cron 需依 chain 分派 adapter）。
    內聯(不 import api.routers)——cron image 是精簡依賴,沒有 fastapi。
    """
    entries: list[dict] = []
    for w in wallets or []:
        if isinstance(w, str):
            entries.append({"address": w, "chain": "ton"})
        elif isinstance(w, dict) and w.get("address"):
            entries.append({
                "address": str(w["address"]),
                "chain": str(w.get("chain", "ton")),
            })
    return entries


async def _process_user(user_id: str, settings: dict) -> tuple[int, int]:
    """處理單一用戶的所有監測錢包。回 (checked, alerts)。"""
    # 路線 C：保留 chain，依 chain 分派 adapter
    wallets = _wallet_entries(settings.get("monitored_wallets", []))
    alerts = 0
    # 模式 B：取使用者的 Etherscan BYOK key（顯式傳 user_id，cron 無 request context）
    user_etherscan_key = None
    try:
        from core.tools.key_resolver import resolve_tool_key

        user_etherscan_key = resolve_tool_key(
            "etherscan",
            official_env="ETHERSCAN_SERVICE_API_KEY",  # 使用者沒設 BYOK 時 fallback 服務 key
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cron_wallet_monitor] resolve etherscan key failed for %s: %s", user_id, exc)

    for entry in wallets:
        addr = entry["address"]
        chain = entry.get("chain", "ton")
        # 依 chain 取 adapter（TON 走既有 engine；EVM 走 EvmAdapter）
        if chain == "ton":
            # 從這個用戶上次處理到的那筆往後全抓（超過一頁也不漏）
            events = await fetch_new_events(addr, user_id=user_id)
            matched = match_rules(addr, events, settings, user_id=user_id)
        elif not WALLET_MONITOR_EVM_ENABLED:
            # 2026-09-04：這個旗標從建立起就沒有任何模組讀它——EVM 路徑
            # 無條件執行。它的值看起來還有防護（沒有 ETHERSCAN_SERVICE_API_KEY
            # 就是 False），實際上完全沒生效，缺 key 時是逐一位址失敗後
            # 記 warning。現在真的接上。
            matched = []
        else:
            # EVM 路徑（路線 C + 模式 B：使用者 BYOK key）
            try:
                from core.wallet_monitor.adapters import get_adapter

                adapter = get_adapter(chain)
                if adapter:
                    raw_events = await adapter.fetch_events(
                        addr, api_key=user_etherscan_key, user_id=user_id
                    )
                    matched = adapter.translate_events(
                        addr, raw_events, settings, user_id=user_id
                    )
                else:
                    matched = []
            except Exception as exc:  # noqa: BLE001
                logger.warning("[cron_wallet_monitor] EVM adapter failed %s: %s", addr[:12], exc)
                matched = []
        if matched:
            sent = await alert_dispatcher.dispatch(user_id, matched, settings)
            alerts += sent
    return len(wallets), alerts


async def run_job() -> dict:
    """主任務。回統計 dict。"""
    if not WALLET_MONITOR_ENABLED:
        logger.info("WALLET_MONITOR_ENABLED=false, skip")
        return {"success": True, "skipped": True, "reason": "feature_disabled"}

    users = _fetch_users_with_monitors()
    if not users:
        logger.info("沒有用戶設定錢包監測")
        return {"success": True, "processed_users": 0, "checked_wallets": 0, "alerts_sent": 0}

    # Entitlement gate（§9.7）：開啟時只處理 active premium 用戶。
    users, skipped = _entitlement_filter(
        users, gate_enabled=WALLET_MONITOR_PREMIUM_GATE_ENABLED
    )
    if skipped:
        logger.info("entitlement gate 跳過 %d 位非 premium 用戶", skipped)
    if not users:
        logger.info("entitlement gate 過濾後無可處理用戶")
        return {
            "success": True,
            "processed_users": 0,
            "checked_wallets": 0,
            "alerts_sent": 0,
            "gate_skipped": skipped,
        }

    logger.info("開始監測 %d 位用戶的錢包", len(users))
    # 並行但限流（避免打爆 TonAPI）
    sem = asyncio.Semaphore(5)

    async def _bounded(uid: str, st: dict) -> tuple[int, int]:
        async with sem:
            try:
                return await _process_user(uid, st)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("  用戶 %s 處理失敗: %s", uid, exc)
                return (0, 0)

    results = await asyncio.gather(*[_bounded(uid, st) for uid, st in users])
    total_wallets = sum(r[0] for r in results)
    total_alerts = sum(r[1] for r in results)
    logger.info(
        "監測完成：用戶 %d / 錢包 %d / 警示 %d",
        len(users), total_wallets, total_alerts,
    )
    return {
        "success": True,
        "processed_users": len(users),
        "checked_wallets": total_wallets,
        "alerts_sent": total_alerts,
        "gate_skipped": skipped,
    }


def main() -> int:
    """Entry point for cron / direct invocation."""
    from core import cron_lock

    lock = cron_lock.acquire("wallet-monitor")
    if lock is None:
        logger.warning("previous wallet monitor run still active; skipping")
        return 0
    try:
        stats = asyncio.run(run_job())
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("錢包監測 cron 嚴重失敗: %s", exc)
        return 1
    logger.info("stats: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
