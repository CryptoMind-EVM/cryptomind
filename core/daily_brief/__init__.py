"""每日早報（留存核心第 1～4 項；docs/plans/2026-09-12-daily-brief-retention-design.md）。

四件事共用同一條管線，不是四個功能：

    cron（每小時）→ schedule 判誰到點 → collect 撈資料 → compose 純文字組稿
      → insight（agent 一句話，可省略）→ send（Telegram＋站內）→ store 記 last_sent_on

- ``schedule``／``compose`` 是純函式，單元測試主要在這兩層。
- ``collect`` 只讀既有資料：帳本 positions／支出、警報觸發通知、事件日曆、自選清單。
- ``insight`` 走使用者 BYOK 模型，沒金鑰就省略那一句——早報照發。
"""
