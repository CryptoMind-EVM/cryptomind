"""從 FinanceDatabase 匯出全球高頻股票符號 → data/global_symbols.json。

用途：
  FinanceDatabase 套件載入要 5-7 秒、佔 238MB 記憶體，不適合放在運行時查詢路徑。
  但它的資料（161k 股票、分類齊全）是好的。本腳本一次性匯出「高頻會問的」
  大型股（Mega/Large Cap），轉成我們自己的輕量 JSON，運行時毫秒級載入。

  匯出後 FinanceDatabase 套件可卸載（只用資料，不依賴運行時）。

篩選條件：
  - market_cap in (Mega Cap, Large Cap) — 全球大型股，高頻被問
  - delisted == False — 排除下市
  - 我們支援的市場：us/tw/hk/jp/kr/in/cn（對齊 SUPPORTED_MARKETS）

輸出格式（data/global_symbols.json）：
  {
    "_meta": {...},
    "symbols": {
      "NVDA": {"name": "NVIDIA Corporation", "market": "us", "country": "United States", "sector": "..."},
      ...
    }
  }

執行：python scripts/export_global_symbols.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 對齊 core/providers/base_provider.py 的 SUPPORTED_MARKETS
COUNTRY_TO_MARKET = {
    "United States": "us",
    "Taiwan": "tw",
    "Hong Kong": "hk",
    "Japan": "jp",
    "South Korea": "kr",
    "India": "in",
    "China": "cn",
}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "global_symbols.json"


def export_global_symbols() -> None:
    """匯出全球大型股符號到 data/global_symbols.json。"""
    import financedatabase as fd

    logger.info("載入 FinanceDatabase Equities（約 5-7 秒）...")
    equities = fd.Equities()
    logger.info(f"總股票數: {len(equities.data)}")

    # 篩選：大型股 + 未下市 + 支援的國家
    df = equities.data[
        equities.data["market_cap"].isin(["Mega Cap", "Large Cap"])
        & (equities.data["delisted"] == False)  # noqa: E712
        & equities.data["country"].isin(COUNTRY_TO_MARKET.keys())
    ].copy()
    logger.info(f"篩選後（大型股 + 支援國家 + 未下市）: {len(df)} 支")

    # 轉成 {symbol: {name, market, country, sector}}
    symbols: dict = {}
    for sym, row in df.iterrows():
        country = row.get("country", "")
        market = COUNTRY_TO_MARKET.get(country, "us")
        symbols[str(sym)] = {
            "name": str(row.get("name", "")).strip(),
            "market": market,
            "country": country,
            "sector": str(row.get("sector", "")).strip() or None,
        }

    # 各市場統計
    market_counts: dict = {}
    for v in symbols.values():
        m = v["market"]
        market_counts[m] = market_counts.get(m, 0) + 1
    logger.info(f"各市場分布: {market_counts}")

    output = {
        "_meta": {
            "description": "全球大型股票符號（從 FinanceDatabase 匯出，運行時輕量查詢用）",
            "source": "FinanceDatabase Equities (Mega/Large Cap, 未下市)",
            "total": len(symbols),
            "markets": market_counts,
            "note": (
                "英文 ticker/公司名的本地快速查詢（第 1 層）。"
                "中文暱稱靠 company_aliases.json（第 0 層），"
                "長尾靠 multi_market_resolver 外部 API（第 2 層）。"
            ),
        },
        "symbols": symbols,
    }

    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    logger.info(f"寫入 {OUTPUT_PATH}: {len(symbols)} 符號, {size_kb:.0f} KB")


if __name__ == "__main__":
    export_global_symbols()
