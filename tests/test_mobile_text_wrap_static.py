"""靜態守衛：手機文字換行修正的 class 不能被改回去（2026-09-12）。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WEB = Path(__file__).resolve().parents[1] / "web"


def test_commodity_sector_card_keeps_price_row_on_one_line():
    js = (WEB / "js" / "commodity.js").read_text(encoding="utf-8")
    assert (
        'class="flex items-baseline justify-between gap-2 mt-0.5 whitespace-nowrap"'
        in js
    ), "商品卡的價格＋漲跌要自己一列且 nowrap，否則真實行情會擠到名稱旁邊斷行"


def test_connection_badges_cannot_be_squeezed():
    for name in ("components/tab-connections.js", "telegram-link.js", "line-link.js"):
        js = (WEB / "js" / name).read_text(encoding="utf-8")
        assert "rounded-full text-xs font-medium shrink-0 whitespace-nowrap" in js, (
            f"{name} 的狀態徽章缺 shrink-0 whitespace-nowrap（會被壓成直排）"
        )

