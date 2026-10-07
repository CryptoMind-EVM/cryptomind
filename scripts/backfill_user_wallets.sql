-- ============================================================================
-- backfill_user_wallets.sql — c041 多鏈身份回填（multichain design Part A, Task A3）
--
-- 把既有「TON 地址直接當 user_id」的帳號回填進 user_wallets（chain='ton'）。
-- tg_* 與 evm_* 開頭的 user_id 不符合 TON friendly 格式，自然被排除。
-- 冪等：ON CONFLICT (chain, address) DO NOTHING，可重複執行。
--
-- 執行方式（design Task A3：先 dry-run 給 DANNY 過目，再 apply）：
--   1. dry-run：   psql "$DATABASE_URL" -f scripts/backfill_user_wallets.sql
--                  （預設只在事務內ROLLBACK，僅輸出對照與筆數）
--   2. 確認無誤後：psql "$DATABASE_URL" -v apply=1 -f scripts/backfill_user_wallets.sql
-- ============================================================================

-- TON friendly 地址：48 字 base64url（EQ.../UQ.../0Q...）
\if :{?apply}
\echo '=== APPLY mode: inserting into user_wallets (committed) ==='
\else
\echo '=== DRY-RUN mode: transaction will be ROLLED BACK. Re-run with -v apply=1 to commit. ==='
\endif

BEGIN;

-- 預覽：將被回填的帳號
SELECT user_id AS ton_address,
       user_id AS will_bind_as,
       (SELECT count(*) FROM user_wallets) AS rows_before
FROM users
WHERE user_id ~ '^[A-Za-z0-9_-]{48}$'
  AND NOT EXISTS (
      SELECT 1 FROM user_wallets w
      WHERE w.chain = 'ton' AND w.address = users.user_id
  )
ORDER BY user_id;

INSERT INTO user_wallets (user_id, chain, address, is_primary)
SELECT user_id, 'ton', user_id, TRUE
FROM users
WHERE user_id ~ '^[A-Za-z0-9_-]{48}$'
ON CONFLICT (chain, address) DO NOTHING;

-- 結果對照
SELECT (SELECT count(*) FROM user_wallets WHERE chain = 'ton') AS ton_rows_after,
       (SELECT count(*) FROM users WHERE user_id ~ '^[A-Za-z0-9_-]{48}$') AS ton_users_total;

\if :{?apply}
COMMIT;
\else
ROLLBACK;
\endif
