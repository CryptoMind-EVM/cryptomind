"""Email 早報（PR-8；docs/plans/2026-09-27-pr8-email-brief-impl.md）。

    設定頁填 email → service.prepare_subscription（存 hash）→ provider 寄確認信
      → /api/email/confirm 點連結 → verified＋早報頻道加 email
      → cron：service.annotate_rows 標 email_active → send_brief_email（HTML＋純文字、一鍵退訂）

- ``provider``：與服務商無關的寄信介面＋Resend REST 實作；設定不全就 fail closed。
- ``store``：``user_email_subscriptions`` 的同步 SQL（API 端用 run_sync 包）。
- ``service``：email 驗證、token、訂閱狀態、送早報。
- ``render``：信件與結果頁（只有 inline CSS；所有使用者內容都 html.escape）。

旗標 ``EMAIL_BRIEF_ENABLED`` 預設關：關的時候除了退訂端點，其他全部不動作。
"""
