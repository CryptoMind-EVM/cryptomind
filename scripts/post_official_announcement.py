#!/usr/bin/env python
"""用「CryptoMind官方」帳號在論壇發公告（可置頂）——之後每次要發公告都用這支。

官方帳號（cryptomind-official）由 scripts/seed_forum_welcome.py 建立，任何登入流程都登入
不了它，所以公告只能從正式機用這支寫進資料庫。內文寫在一個 UTF-8 的 Markdown 檔
（建議放 docs/announcements/，留紀錄），標題用 --title 給。

- 預設只印出會做什麼（dry-run），加 --apply 才寫入
- 可重複執行：同標題的官方文章已存在就跳過（要改內容請到後台或換標題）
- 置頂用 --pin；之後要取消置頂，到管理後台的論壇管理操作

用法（正式機；這支不在 app image 裡，要跟內文檔一起掛進一次性容器）：
    docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps -T \\
      -v "$PWD/scripts/post_official_announcement.py:/app/scripts/post_official_announcement.py:ro" \\
      -v "$PWD/docs/announcements:/app/docs/announcements:ro" \\
      app python scripts/post_official_announcement.py \\
        --title "服務條款與隱私政策更新（2026-09-27）" \\
        --content-file docs/announcements/2026-09-27-legal-update.md --pin   # 先看計畫；加 --apply 才寫入
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update  # noqa: E402

from core.orm.forum_repo import forum_repo  # noqa: E402
from core.orm.models import Board, Post, User  # noqa: E402
from core.orm.session import using_session  # noqa: E402

OFFICIAL_USER_ID = "cryptomind-official"


async def main(args: argparse.Namespace) -> None:
    content = Path(args.content_file).read_text(encoding="utf-8").strip()
    if not content:
        raise SystemExit(f"內文是空的：{args.content_file}")
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    async with using_session() as s:
        if (
            await s.execute(select(User.user_id).where(User.user_id == OFFICIAL_USER_ID))
        ).scalar_one_or_none() is None:
            raise SystemExit(
                f"官方帳號 {OFFICIAL_USER_ID} 不存在——先跑 scripts/seed_forum_welcome.py --apply"
            )
        board_id = (
            await s.execute(select(Board.id).where(Board.slug == args.board))
        ).scalar_one_or_none()
        if board_id is None:
            raise SystemExit(f"找不到看板 slug={args.board!r}")

        existing = (
            await s.execute(
                select(Post.id).where(
                    Post.user_id == OFFICIAL_USER_ID, Post.title == args.title
                )
            )
        ).scalar_one_or_none()
        if existing:
            print(f"[公告] 已存在 #{existing}：{args.title}，跳過")
            return

        print(
            f"[公告] 看板 {args.board}／分類 {args.category}／標籤 {tags}"
            f"{'／置頂' if args.pin else ''}\n標題：{args.title}\n---\n{content}\n---"
        )
        if not args.apply:
            print("以上為預覽，加 --apply 才會寫入")
            return

        result = await forum_repo.create_post(
            board_id=board_id,
            user_id=OFFICIAL_USER_ID,
            category=args.category,
            title=args.title,
            content=content,
            tags=tags,
            session=s,
        )
        if not result.get("success"):
            raise SystemExit(f"發文失敗：{result}")
        if args.pin:
            await s.execute(
                update(Post).where(Post.id == result["post_id"]).values(is_pinned=1)
            )
        print(f"完成（已寫入）：文章 #{result['post_id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--title", required=True, help="公告標題（同標題已存在會跳過）")
    parser.add_argument("--content-file", required=True, help="UTF-8 Markdown 內文檔")
    parser.add_argument("--board", default="crypto", help="看板 slug（預設 crypto）")
    parser.add_argument("--category", default="news", help="文章分類（預設 news）")
    parser.add_argument("--tags", default="公告", help="逗號分隔的標籤（預設：公告）")
    parser.add_argument("--pin", action="store_true", help="發文後置頂")
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只預覽）")
    asyncio.run(main(parser.parse_args()))
