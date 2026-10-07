"""定時任務：每日早報（每小時跑；本地時命中 send_hour 的人才送）。

設計：docs/plans/2026-09-12-daily-brief-retention-design.md §7

    候選人（綁 Telegram 或設過偏好）→ schedule.due_users 判到點
      → collect（沒個人內容就補市場概況）→ compose（仍空＝不送）→ insight（可省略）
      → send → store.mark_sent

- 單一使用者失敗不中斷批次（per-user try/except）。
- 並行產生（``DAILY_BRIEF_CONCURRENCY``，預設 4）：每人一次 LLM 呼叫，逐一跑的話人數
  一多就跑超過一小時的排程。只有 LLM／Telegram／報價這些 async I/O 會重疊；同步 DB
  呼叫都在 event loop 這條執行緒上輪流跑，每次各自向連線池借還（DatabaseBase），不共用連線。
- 冪等：``last_sent_on`` 擋同一本地日重送；沒內容可送也記今天，避免每小時重試。
- 手動：``--user <id> --force`` 忽略時間直接送一份；``--dry-run`` 只印不送。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [daily-brief] %(message)s")
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


async def send_one(
    prefs, now_utc: datetime, *, dry_run: bool = False, force: bool = False
) -> str:
    """回傳結果代碼：sent / empty / failed / skipped（今天這份別的排程已經在送或送過）。"""
    from core.daily_brief import store
    from core.daily_brief.schedule import local_today

    today = local_today(prefs, now_utc)
    # 產生內容前先搶下今天這一份（手動 --force／--dry-run 不搶）：檔案鎖只管同一個容器
    if not dry_run and not force:
        if not store.claim_day(prefs.user_id, today, timezone=prefs.timezone):
            return "skipped"
    state = {"delivered_any": False}
    try:
        return await _compose_and_send(prefs, now_utc, today, state, dry_run=dry_run)
    except BaseException:
        # 一則都還沒送出去就出錯：把今天這份還回去（送出過一則就不還，免得重複）
        if not dry_run and not force and not state["delivered_any"]:
            store.release_day(prefs.user_id, today, prefs.last_sent_on)
        raise


async def _compose_and_send(prefs, now_utc: datetime, today, state: dict, *, dry_run: bool) -> str:
    from core.daily_brief import store
    from core.daily_brief.collect import collect_brief_data
    from core.daily_brief.compose import compose_brief
    from core.daily_brief.insight import generate_insight
    from core.daily_brief.send import send_baseapp, send_inapp, send_telegram_text
    from core.i18n import t

    data = await collect_brief_data(prefs, now_utc)
    text = compose_brief(data)
    if not text:
        if not dry_run:
            store.mark_sent(prefs.user_id, today, timezone=prefs.timezone)
        return "empty"

    insight = await generate_insight(
        prefs.user_id, prefs.membership_tier, prefs.language, text
    )
    if insight:
        from core.model_config import PLATFORM_FREE_MODEL_LABEL

        data.insight = insight
        data.insight_model = PLATFORM_FREE_MODEL_LABEL
        text = compose_brief(data) or text

    if dry_run:
        print(f"--- {prefs.user_id} ({prefs.language}, {prefs.timezone}) ---\n{text}\n")
        return "sent"

    delivered = False
    if "telegram" in prefs.channels and prefs.telegram_id is not None:
        delivered = await send_telegram_text(prefs.telegram_id, text) or delivered
        state["delivered_any"] = state["delivered_any"] or delivered
    # Base App 推播只是「有早報了」的敲門，內容在站內通知——勾了 baseapp 也要寫站內那份，
    # 不然只勾 Base App 的人點開什麼都沒有
    if "inapp" in prefs.channels or "baseapp" in prefs.channels:
        delivered = (
            send_inapp(
                prefs.user_id,
                t("ui_messages.daily_brief.inapp_title", prefs.language),
                text,
                on_date=today.isoformat(),
            )
            or delivered
        )
        state["delivered_any"] = state["delivered_any"] or delivered
    if "baseapp" in prefs.channels:
        # 宿主推播只是「有早報了」的敲門，內容在站內通知／Telegram；沒 token 的人這裡是 no-op
        delivered = (
            send_baseapp(
                prefs.user_id,
                t("ui_messages.daily_brief.push_title", prefs.language),
                t("ui_messages.daily_brief.push_body", prefs.language),
                on_date=today.isoformat(),
            )
            or delivered
        )
        state["delivered_any"] = state["delivered_any"] or delivered
    if "email" in prefs.channels and prefs.email_active:
        # Email 版拿掉「回覆這則訊息／回覆 /brief off」那句 Telegram 專用提示；
        # 免責也拿掉（信末本來就有），信末另有退訂連結
        from dataclasses import replace

        from core.email_brief.service import send_brief_email

        email_text = (
            compose_brief(replace(data, telegram_hint=False, disclaimer=False)) or text
        )
        delivered = (
            await send_brief_email(
                prefs.user_id, prefs.language, email_text, on_date=today.isoformat()
            )
            or delivered
        )
        state["delivered_any"] = state["delivered_any"] or delivered
    store.mark_sent(prefs.user_id, today, timezone=prefs.timezone)
    return "sent" if delivered else "failed"


async def run(
    now_utc: Optional[datetime] = None,
    *,
    only_user: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    from core.daily_brief import store
    from core.daily_brief.schedule import due_users, effective_prefs

    now_utc = now_utc or datetime.now(timezone.utc)
    if only_user:
        row = store.get_prefs_row(only_user)
        rows = [row] if row else []
    else:
        rows = store.list_candidates()
    # Email 早報（PR-8）：旗標關或設定不全時原樣回傳、不查訂閱表
    from core.email_brief.service import annotate_rows

    rows = annotate_rows(rows)

    if force:
        targets = []
        for row in rows:
            try:
                targets.append(effective_prefs(row))
            except Exception:  # noqa: BLE001
                continue
    else:
        targets = due_users(rows, now_utc)

    summary = {
        "candidates": len(rows),
        "due": len(targets),
        "sent": 0,
        "empty": 0,
        "failed": 0,
        "skipped": 0,
    }
    sem = asyncio.Semaphore(concurrency())

    async def _one(prefs) -> str:
        async with sem:
            try:
                return await send_one(prefs, now_utc, dry_run=dry_run, force=force)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 — 單人失敗不中斷批次
                logger.warning("user=%s failed: %s", prefs.user_id, exc)
                return "failed"

    for outcome in await asyncio.gather(*(_one(p) for p in targets)):
        summary[outcome] = summary.get(outcome, 0) + 1
    logger.info(
        "candidates=%d due=%d sent=%d empty=%d failed=%d skipped=%d",
        summary["candidates"],
        summary["due"],
        summary["sent"],
        summary["empty"],
        summary["failed"],
        summary["skipped"],
    )
    return summary


DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 16


def concurrency() -> int:
    """同時產生幾份早報（DAILY_BRIEF_CONCURRENCY）。壞值用預設；1＝逐一跑；上限 16。"""
    import os

    try:
        value = int(os.getenv("DAILY_BRIEF_CONCURRENCY", str(DEFAULT_CONCURRENCY)))
    except ValueError:
        return DEFAULT_CONCURRENCY
    return max(1, min(MAX_CONCURRENCY, value))


def cron_enabled() -> bool:
    """DAILY_BRIEF_ENABLED=false 讓 cron 整批停掉（首次全體推播前、或出事時的開關）；
    --user 指定單人時不受影響，方便試送。預設開；後台設定中心可覆寫。"""
    from core.feature_flags import daily_brief_enabled

    return daily_brief_enabled()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日早報 cron（每小時執行）")
    ap.add_argument("--user", help="只處理這個 user_id")
    ap.add_argument("--force", action="store_true", help="忽略時間與今日已送，直接送")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容不送")
    args = ap.parse_args(argv)
    # cron-worker 沒有常駐程式：每小時這支跑的時候回報一次旗標快照（後台設定中心比對各服務用）
    from core.feature_flags import store_service_snapshot

    store_service_snapshot("cron-worker")
    if not args.user and not cron_enabled():
        logger.info("DAILY_BRIEF_ENABLED is off; skipping batch")
        return 0
    from core import cron_lock

    lock = cron_lock.acquire("daily-brief")
    if lock is None:
        logger.warning("previous daily brief run still active; skipping")
        return 0
    try:
        summary = asyncio.run(
            run(only_user=args.user, force=args.force, dry_run=args.dry_run)
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("daily brief cron crashed: %s", exc)
        return 1
    return exit_code(summary)


def exit_code(summary: dict) -> int:
    """整批都失敗（一個都沒送出）要回非 0，不然 cron 看起來一切正常。"""
    failed = summary.get("failed", 0)
    delivered = summary.get("sent", 0) + summary.get("empty", 0)
    return 1 if failed and not delivered else 0


if __name__ == "__main__":
    sys.exit(main())
