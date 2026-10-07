"""
聊天室 AI 助理：把「看得到的訊息」組成給模型的上下文（純函式）。

- 聊天紀錄包在 <chat_log>…</chat_log>，規則寫明是資料不是指令；訊息與暱稱裡的 chat_log 標籤轉成
  [chat_log]、換行壓成空白——別人的訊息不能假裝結束區塊或偽造一行別人說的話。
- token 用估的（CJK 1.5、ASCII 每 2.5 字元 1、其他文字如俄文每 1.5 字元 1），不引 tokenizer 套件。
  2026-10-01 對 cl100k 校正：中英俄混合的聊天行落在 0.7～1.4 倍；本地模型（Qwen 系）中文更省，所以偏保守。
- recent／message：只取最新、塞得進預算的；長範圍（unread／24h／3d）超過預算就從最新往回切段，
  最多 MAX_CHUNKS 段，更早的捨棄（truncated）。
"""

from __future__ import annotations

import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 本地模型每個位置 32K。2026-10-01 實測（cl100k）：訪客 agent prompt＋助理規則 8.2K、29 個工具 schema 5.1K，
# 再預留工具結果約 6K、追問歷史 2K、問題 0.8K、輸出 1.5K → 聊天紀錄放 5K 還有約 3K 餘裕
RECENT_BUDGET_TOKENS = 5000
HISTORY_BUDGET_TOKENS = 2000
CHUNK_BUDGET_TOKENS = 6000  # 濃縮呼叫沒有 agent prompt／工具，每段可以大一點
MAX_CHUNKS = 5
DEFAULT_TZ = "Asia/Taipei"

_CJK_RE = re.compile(r"[　-鿿가-힯豈-﫿＀-￯]")
_TAG_RE = re.compile(r"<\s*/?\s*chat_log\s*>", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s*[\r\n]+\s*")


def estimate_tokens(text: str) -> int:
    text = text or ""
    cjk = len(_CJK_RE.findall(text))
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - cjk - ascii_chars
    return math.ceil(cjk * 1.5 + ascii_chars / 2.5 + other / 1.5)


def _line_cost(line: str) -> int:
    return estimate_tokens(line) + 1  # 換行與角色開銷


def escape_chat_text(text: str) -> str:
    return _SPACE_RE.sub(" ", _TAG_RE.sub("[chat_log]", text or "")).strip()


def resolve_tz(name) -> ZoneInfo:
    try:
        return (
            ZoneInfo(name) if isinstance(name, str) and name else ZoneInfo(DEFAULT_TZ)
        )
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def format_line(line: dict, tz: ZoneInfo) -> str:
    stamp = line["created_at"].astimezone(tz).strftime("%m-%d %H:%M")
    text = escape_chat_text(line["text"])
    if line["type"] == "system":
        return f"[{stamp}] * {text}"
    name = escape_chat_text(line["name"] or "someone")
    if line.get("reply_to_name"):
        name = f"{name} (reply to {escape_chat_text(line['reply_to_name'])})"
    return f"[{stamp}] {name}: {text}"


def chat_block(lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "(no messages)"
    return f"<chat_log>\n{body}\n</chat_log>"


def take_recent(lines: list[str], budget: int) -> list[str]:
    """從最新往回取到預算為止（最新一則就算超過也給）"""
    out: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = _line_cost(line)
        if out and used + cost > budget:
            break
        out.append(line)
        used += cost
    out.reverse()
    return out


def split_chunks(
    lines: list[str], chunk_budget: int, max_chunks: int
) -> tuple[list[list[str]], bool]:
    """從最新往回切段，每段不超過 chunk_budget；超過 max_chunks 段就丟掉更早的。回傳由舊到新"""
    chunks: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = _line_cost(line)
        if current and used + cost > chunk_budget:
            chunks.append(current)
            current, used = [], 0
        current.append(line)
        used += cost
    if current:
        chunks.append(current)
    truncated = len(chunks) > max_chunks
    kept = [list(reversed(c)) for c in chunks[:max_chunks]]
    kept.reverse()
    return kept, truncated


def plan_context(
    lines: list[str],
    range_: str,
    budget: int = RECENT_BUDGET_TOKENS,
    chunk_budget: int = CHUNK_BUDGET_TOKENS,
    max_chunks: int = MAX_CHUNKS,
) -> dict:
    """{mode: direct|chunks, lines, chunks, truncated}"""
    total = sum(_line_cost(line) for line in lines)
    if range_ in ("recent", "message") or total <= budget:
        kept = take_recent(lines, budget)
        return {
            "mode": "direct",
            "lines": kept,
            "chunks": [],
            "truncated": len(kept) < len(lines),
        }
    chunks, truncated = split_chunks(lines, chunk_budget, max_chunks)
    return {"mode": "chunks", "lines": [], "chunks": chunks, "truncated": truncated}


def trim_history(
    history: list[dict], budget: int = HISTORY_BUDGET_TOKENS
) -> list[dict]:
    """抽屜追問：從最新往回留到預算為止，而且從 user 那一則開始（不要半輪）"""
    kept: list[dict] = []
    used = 0
    for item in reversed(history):
        cost = estimate_tokens(item["content"])
        if used + cost > budget:
            break
        kept.append(item)
        used += cost
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept
