"""早報偏好分出「使用者自己設的」與「系統記帳用的」列：user_brief_prefs.user_set（c063）

2026-09-29：mark_sent（以及 c063 同批的 claim_day）替沒偏好列的人自動建一列來記
last_sent_on，但 effective_prefs 與 overview_eligible 把「有列」當成「自己設過」——
1. 空早報補市場概況原本只給新帳號與自己設過的人（954bac7，避免對很久沒用的人首次主動推播），
   但第一次空早報就被自動建列，隔天起就全變成「自己設過」，限制從沒生效過。
2. 自動建的列 channels 是 DB 預設 {telegram,inapp}，程式預設多了 baseapp，這些人一直收不到
   Base App 推播；解綁 Telegram 後 enabled 仍是 TRUE。
之後只有設定頁、bot /brief、Email 訂閱確認會把 user_set 設 TRUE；自動建的列只拿來記 last_sent_on。

回填（寧可少送，不要誤推）：值跟 DB 預設不一樣的（一定是自己改過）、沒綁 Telegram 的
（沒綁的人不會被自動建列）、有啟用中 Base App 通知 token 的（9/13 後的活躍用戶；當自動建的會被補上
baseapp 頻道，萬一是自己取消勾選的就等於幫他打開）算 TRUE；其他當自動建的。自己存過但全用
預設值的人會被當成自動建的，差別只有空早報那天不補市場概況、頻道多了 baseapp（沒 token 不會送）。

Rollback：``alembic downgrade c062``（拿掉欄位）——程式也要一起退回 c063 之前，不然所有列都會被
當成自動建的（自己設過的偏好全部失效）。
"""

from alembic import op

revision = "c063"
down_revision = "c062"
branch_labels = None
depends_on = None


# 測試直接拿條件去 SELECT（tests/test_daily_brief_cron.py），不用在測試裡 UPDATE 整張表
LOOKS_USER_SET = """(
           p.enabled = FALSE
        OR p.send_hour <> 8
        OR p.channels <> '{telegram,inapp}'::TEXT[]
        OR p.include_spend = FALSE
        OR p.include_macro = FALSE
        OR NOT EXISTS (SELECT 1 FROM telegram_bindings b WHERE b.user_id = p.user_id)
        OR EXISTS (
            SELECT 1 FROM miniapp_notification_tokens t
            WHERE t.user_id = p.user_id AND t.enabled
        )
    )"""
BACKFILL_SQL = f"UPDATE user_brief_prefs p SET user_set = TRUE WHERE {LOOKS_USER_SET}"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_brief_prefs ADD COLUMN IF NOT EXISTS "
        "user_set BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.execute("ALTER TABLE user_brief_prefs DROP COLUMN IF EXISTS user_set")
