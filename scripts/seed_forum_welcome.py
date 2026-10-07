#!/usr/bin/env python
"""論壇開放前的官方置頂文（2026-09-26 DANNY：論壇與好友開放前準備）。

建立一個標明「官方」的帳號，發幾篇平台使用說明／社群規範並置頂——
讓第一批使用者進論壇時不是空的，也先看到規則。內容只講平台怎麼用，
不含任何行情判斷或喊單。

- 預設只印出會做什麼（dry-run），加 --apply 才寫入
- 可重複執行：帳號已存在就沿用；同帳號同標題的文章已存在就跳過
- 官方帳號的 user_id 不是 evm_/tg_ 開頭，任何登入流程都登入不了它；role 維持 user

用法（正式機）：這支是一次性維運腳本，不在 .dockerignore 的 scripts 白名單裡、
不在 app image 內，要把檔案掛進一次性容器執行（2026-09-26 已在正式機跑過）：
    docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps -T \\
      -v "$PWD/scripts/seed_forum_welcome.py:/app/scripts/seed_forum_welcome.py:ro" \\
      app python scripts/seed_forum_welcome.py            # 先看計畫；加 --apply 才寫入
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update  # noqa: E402

from core.orm.forum_repo import forum_repo  # noqa: E402
from core.orm.models import Board, Post, User  # noqa: E402
from core.orm.session import using_session  # noqa: E402

OFFICIAL_USER_ID = "cryptomind-official"
OFFICIAL_USERNAME = "CryptoMind官方"
BOARD_SLUG = "crypto"

GUIDELINES_URL = "/static/legal/community-guidelines.html"

SEED_POSTS = [
    {
        "category": "tutorial",
        "title": "歡迎來到 CryptoMind 論壇：新手使用指南",
        "tags": ["新手", "指南"],
        "pinned": True,
        "content": f"""歡迎！這裡是 CryptoMind 的加密貨幣討論區，分享你的觀點、提問、教學與心得都很歡迎。

## 文章分類
- **分析**：你對某個幣種或市場的看法與依據
- **請益**：不懂就問，新手問題也沒關係
- **教學**：工具、錢包、鏈上操作的步驟分享
- **新聞**：值得討論的產業消息（請附來源）
- **閒聊**：輕鬆聊
- **心得**：交易或使用經驗的回顧

## 怎麼參與
1. **發文**：登入後點「發文」，選分類、寫標題與內容即可。內文支援 Markdown。
2. **推／噓與回覆**：在文章底下表態或留言。免費會員每天的發文與回覆數各有上限，Premium 會員不限。
3. **打賞**：喜歡某篇文章，可以用 Base 鏈上的 USDC 打賞作者（需要先在「設定」綁定 EVM 錢包）。
4. **好友與私訊**：到作者的個人頁按「加好友」，互為好友後就能私訊。
5. **檢舉**：每篇文章與留言都有檢舉按鈕，管理員審核後會隱藏違規內容。

## 請先讀社群規範
發文前請花一分鐘看[社群規範]({GUIDELINES_URL})，重點整理在置頂的〈社群規範重點與防詐提醒〉。

> 論壇內容僅代表作者個人觀點，不構成投資建議。投資有風險，請自行判斷。""",
    },
    {
        "category": "news",
        "title": "社群規範重點與防詐提醒",
        "tags": ["公告", "防詐"],
        "pinned": True,
        "content": f"""為了讓討論有品質、也保護大家的資產，請遵守以下規則（完整版見[社群規範]({GUIDELINES_URL})）：

## 請不要
- **喊單或操縱市場**：沒有依據的「必漲」「梭哈」、集體拉盤、帶單收費
- **廣告與推薦碼**：交易所邀請碼、付費群、空投拉人頭
- **詐騙連結**：假冒錢包、假空投、要求連接錢包簽名的不明網站
- **人身攻擊與洩漏他人隱私**
- **冒充官方或他人**

## 防詐提醒
- CryptoMind 官方**永遠不會**私訊跟你要助記詞、私鑰，或要你轉帳「驗證」。
- 任何要你「先匯款才能領獎」的訊息都是詐騙。
- 看到可疑內容請直接用檢舉按鈕，管理員會處理。

違規內容會被隱藏，情節嚴重或累犯的帳號會被限制使用。""",
    },
    {
        "category": "tutorial",
        "title": "用 AI 助理做功課：提問範例與注意事項",
        "tags": ["AI", "新手"],
        "pinned": False,
        "content": """CryptoMind 的 AI 助理可以幫你整理市場資訊，發文前先做點功課，討論會更有料。

## 提問範例
- 「幫我看 BTC 目前的技術面」
- 「ETH 最近有什麼重要新聞？」
- 「解釋一下什麼是資金費率」

回到首頁的對話框直接輸入即可。

## 注意事項
- AI 的回答是**資訊整理**，不是投資建議；數據可能有延遲，重要決定前請自己再確認。
- 把 AI 的內容貼到論壇時，請加上你自己的看法，並標明哪些是 AI 整理的。
- 不要把助記詞、私鑰、交易所 API 金鑰貼到任何地方（包含對話框與論壇）。""",
    },
]


async def _get_board_id(s) -> int:
    board_id = (
        await s.execute(select(Board.id).where(Board.slug == BOARD_SLUG))
    ).scalar_one_or_none()
    if board_id is None:
        raise SystemExit(f"找不到看板 slug={BOARD_SLUG!r}，請先確認 boards 表")
    return board_id


async def main(apply: bool) -> None:
    async with using_session() as s:
        board_id = await _get_board_id(s)

        user = (
            await s.execute(select(User).where(User.user_id == OFFICIAL_USER_ID))
        ).scalar_one_or_none()
        if user is None:
            taken = (
                await s.execute(
                    select(User.user_id).where(User.username == OFFICIAL_USERNAME)
                )
            ).scalar_one_or_none()
            if taken:
                raise SystemExit(
                    f"使用者名稱 {OFFICIAL_USERNAME!r} 已被 {taken} 使用，請先處理"
                )
            print(f"[帳號] 建立 {OFFICIAL_USER_ID}（{OFFICIAL_USERNAME}）")
            if apply:
                s.add(
                    User(
                        user_id=OFFICIAL_USER_ID,
                        username=OFFICIAL_USERNAME,
                        display_name=OFFICIAL_USERNAME,
                        auth_method="system",
                        role="user",
                        membership_tier="free",
                        is_active=True,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                await s.flush()
        else:
            print(f"[帳號] 已存在 {OFFICIAL_USER_ID}（{user.username}），沿用")

        # 論壇列表是新→舊（置頂也一樣），反向發文讓 SEED_POSTS 第一篇（歡迎文）排最上面
        for post in reversed(SEED_POSTS):
            existing = (
                await s.execute(
                    select(Post.id).where(
                        Post.user_id == OFFICIAL_USER_ID, Post.title == post["title"]
                    )
                )
            ).scalar_one_or_none()
            if existing:
                print(f"[文章] 已存在 #{existing}：{post['title']}，跳過")
                continue
            print(f"[文章] 發布{'並置頂' if post['pinned'] else ''}：{post['title']}")
            if not apply:
                continue
            result = await forum_repo.create_post(
                board_id=board_id,
                user_id=OFFICIAL_USER_ID,
                category=post["category"],
                title=post["title"],
                content=post["content"],
                tags=post["tags"],
                session=s,
            )
            if not result.get("success"):
                raise SystemExit(f"發文失敗：{result}")
            if post["pinned"]:
                await s.execute(
                    update(Post).where(Post.id == result["post_id"]).values(is_pinned=1)
                )

    print("完成（已寫入）" if apply else "以上為預覽，加 --apply 才會寫入")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只預覽）")
    asyncio.run(main(parser.parse_args().apply))
