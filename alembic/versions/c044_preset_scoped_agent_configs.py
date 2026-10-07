"""per-agent 設定改為 preset 範圍（c044，根治 Model Mixer Step 1/2 的作用域歧義）

問題：``user_agent_configs`` 主鍵是 ``(user_id, agent_id)``——per-agent 的
模型與工具是**使用者層**的，preset 沒有自己的一份。所以「快速查價」與
「深度盡調」兩個 preset 共用同一組設定，改一個另一個跟著變，而 UI 上完全
看不出來。

治標的做法是在 preset 表加一欄、preset 沒設就退回使用者層——那會做出
「兩個地方能設同一件事、哪個贏看不見」的結構，正是這個專案反覆出事的那
一類（2026-09-04 一天內就修掉三個幽靈旗標、一個空包彈排除項）。

根治：**per-agent 設定只存在於 preset**。主鍵改成 ``(preset_id, agent_id)``，
外鍵指向 ``user_agent_presets`` 並 ON DELETE CASCADE——沒有 fallback、沒有
哨兵值，preset 刪掉時它的設定跟著消失。

回填是行為保持的：今天所有 preset 共用同一份設定，所以把每個使用者的既有
列**複製進他自己的每一個 preset**，執行期結果逐字不變。

沒有任何 preset 的使用者，他的列現在就已經是死的——``api/routers/analysis.py``
解析不到 preset 就 ``return None``，那些設定從來沒有生效過。直接丟棄。

Rollback：downgrade 把每個使用者「預設 preset」（沒有的話取最早建立的那個）
的列收回成使用者層，其餘 preset 的列丟棄——因為使用者層放不下多份。
回滾 runbook：先回滾部署 → ``alembic downgrade c043``。
"""

from alembic import op

revision = "c044"
down_revision = "c043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 新欄（先 nullable，回填後才收緊）
    op.execute("ALTER TABLE user_agent_configs ADD COLUMN IF NOT EXISTS preset_id TEXT")

    # 2) 回填：每個使用者的既有列 × 他的每一個 preset。
    #    先把原列標到「第一個 preset」，其餘 preset 用 INSERT 複製，
    #    這樣不需要暫存表也不會丟資料。
    op.execute("""
        WITH first_preset AS (
            SELECT DISTINCT ON (user_id) user_id, preset_id
            FROM user_agent_presets
            ORDER BY user_id, is_default DESC, created_at ASC
        )
        UPDATE user_agent_configs c
        SET preset_id = f.preset_id
        FROM first_preset f
        WHERE c.user_id = f.user_id AND c.preset_id IS NULL
    """)
    # 3) **先卸舊主鍵再複製**——順序反了的話，複製第二個 preset 的列會撞
    #    仍然存在的 PRIMARY KEY (user_id, agent_id)。如果再配上
    #    ON CONFLICT DO NOTHING，資料就會靜默消失而遷移回報成功
    #    （2026-09-04 本檔第一版實測踩到：兩個 preset 只回填了一個）。
    op.execute("ALTER TABLE user_agent_configs DROP CONSTRAINT IF EXISTS user_agent_configs_pkey")

    # 4) 其餘 preset 各複製一份。**不加 ON CONFLICT**：此刻已無唯一約束，
    #    真的撞上代表前提錯了，要讓它大聲失敗而不是吞掉。
    op.execute("""
        INSERT INTO user_agent_configs
            (user_id, agent_id, preset_id, model_selection, tools, skills,
             created_at, updated_at)
        SELECT c.user_id, c.agent_id, p.preset_id, c.model_selection, c.tools,
               c.skills, c.created_at, c.updated_at
        FROM user_agent_configs c
        JOIN user_agent_presets p ON p.user_id = c.user_id
        WHERE c.preset_id IS NOT NULL AND p.preset_id <> c.preset_id
    """)

    # 5) 沒有任何 preset 的使用者：其列本來就不會被執行期讀到，丟棄。
    op.execute("DELETE FROM user_agent_configs WHERE preset_id IS NULL")

    # 6) 新主鍵 + 外鍵。user_id 保留（查詢與稽核方便），但不再是鍵的一部分。
    op.execute("ALTER TABLE user_agent_configs ALTER COLUMN preset_id SET NOT NULL")
    op.execute("""
        ALTER TABLE user_agent_configs
        ADD CONSTRAINT user_agent_configs_pkey PRIMARY KEY (preset_id, agent_id)
    """)
    op.execute("""
        ALTER TABLE user_agent_configs
        ADD CONSTRAINT fk_user_agent_configs_preset
        FOREIGN KEY (preset_id) REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_agent_configs_user "
        "ON user_agent_configs(user_id)"
    )


def downgrade() -> None:
    # 使用者層放不下多份：只留「預設 preset」（沒有則取最早建立）的那一份。
    op.execute("DROP INDEX IF EXISTS idx_user_agent_configs_user")
    op.execute("ALTER TABLE user_agent_configs DROP CONSTRAINT IF EXISTS fk_user_agent_configs_preset")
    op.execute("""
        DELETE FROM user_agent_configs c
        USING user_agent_presets p
        WHERE c.preset_id = p.preset_id
          AND c.preset_id <> (
              SELECT preset_id FROM user_agent_presets
              WHERE user_id = p.user_id
              ORDER BY is_default DESC, created_at ASC
              LIMIT 1
          )
    """)
    op.execute("ALTER TABLE user_agent_configs DROP CONSTRAINT IF EXISTS user_agent_configs_pkey")
    op.execute("""
        ALTER TABLE user_agent_configs
        ADD CONSTRAINT user_agent_configs_pkey PRIMARY KEY (user_id, agent_id)
    """)
    op.execute("ALTER TABLE user_agent_configs DROP COLUMN IF EXISTS preset_id")
