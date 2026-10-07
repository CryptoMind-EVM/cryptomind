"""早報組稿（純函式、零 LLM）。

規則（design §3）：
- 沒有的段落不出現；全部都沒有 → 回 None（不推「今天沒事」的垃圾訊息）。
- 數字全部來自帳本與工具；agent 只寫「一句話」那段（insight，由呼叫端先驗過來源）。
- 上限 1,200 字元：先砍自選清單，再砍持倉多出來的列。
- 每月 1 日多一段上月總結。
- PR-7（2026-09-27）：上次早報後評完分的判斷列成「🎯」段（算個人內容）；
  沒有任何個人內容也沒自選時，改用市場概況（collect 只在這時才抓），新用戶第一天就收得到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from core.i18n import t as _t

MAX_CHARS = 1200
MAX_POSITION_ROWS = 8
MAX_WATCHLIST_ROWS = 10
MAX_CALL_ROWS = 5


@dataclass
class BriefData:
    language: str
    date_local: date
    display_name: Optional[str] = None
    base_currency: str = "TWD"
    positions: list = field(
        default_factory=list
    )  # {symbol, market, change_pct, note, current_price}
    alerts: list = field(default_factory=list)  # {title, body, created_at}
    events: list = field(default_factory=list)  # {event_date, title, source}
    spend_yesterday: list = field(default_factory=list)  # [{category, total}]
    spend_mtd: Optional[float] = None
    spend_prev_same_period: Optional[float] = None
    # 使用者自己挑的標的（user_watchlist，持倉已列的不重複）：{symbol, market, price, change_pct}
    watchlist: list = field(default_factory=list)
    monthly: Optional[dict] = (
        None  # {trades, expense_total, expense_prev_total, top_expense: [(cat, total)]}
    )
    insight: Optional[str] = None
    # 寫「一句話」的模型對外名稱（平台本地模型，PLATFORM_FREE_MODEL_LABEL）；信末標示用
    insight_model: Optional[str] = None
    telegram_hint: bool = True
    # 「僅供參考，非投資建議」：Email 信末本來就有，那一版關掉避免重複
    disclaimer: bool = True
    alert_suggestions: list = field(
        default_factory=list
    )  # 有持倉沒警報的 symbol（Phase C）
    # 上次早報後評完分的判斷（judgment_scores）：{kind, symbol, side, horizon_days, raw_pct,
    # bench_symbol, bench_pct, excess_pct, hit}
    scored_calls: list = field(default_factory=list)
    # 會空時的市場概況：{quotes: [{label, price, change_pct}], next_event: {date, title} | None}
    market_overview: Optional[dict] = None


def _tr(key: str, lang: str, **vars: Any) -> str:
    return _t(f"ui_messages.daily_brief.{key}", lang, **vars)


def fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "-"
    v = float(value)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return ""
    v = float(value)
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "＝")
    return f"{v:+.1f}%  {arrow}"


def _positions_section(data: BriefData, lang: str, limit: int) -> Optional[str]:
    if not data.positions:
        return None
    rows = []
    for p in data.positions[:limit]:
        pct = fmt_pct(p.get("change_pct")) if p.get("change_pct") is not None else ""
        note = f"  {p['note']}" if p.get("note") else ""
        rows.append(f"  {p['symbol']:<8} {pct}{note}".rstrip())
    more = len(data.positions) - limit
    if more > 0:
        rows.append("  " + _tr("more_rows", lang, n=more))
    title = _tr("positions_title", lang, n=len(data.positions), ccy=data.base_currency)
    return title + "\n" + "\n".join(rows)


def _alerts_section(data: BriefData, lang: str) -> Optional[str]:
    if not data.alerts:
        return None
    rows = [f"  ・{a.get('body') or a.get('title')}" for a in data.alerts[:5]]
    return _tr("alerts_title", lang) + "\n" + "\n".join(rows)


def _alert_suggestion_line(data: BriefData, lang: str) -> Optional[str]:
    """Phase C：帳本有標的但沒設警報 → 早報末尾問一句（第一版不自動建）。"""
    if not data.positions or not data.alert_suggestions:
        return None
    syms = "、".join(str(s) for s in data.alert_suggestions[:2])
    return _tr("alert_suggest", lang, symbols=syms)


def _events_section(data: BriefData, lang: str) -> Optional[str]:
    if not data.events:
        return None
    rows = []
    for e in data.events[:6]:
        d = e.get("event_date")
        if isinstance(d, date):
            if d == data.date_local:
                when = _tr("event_today", lang)
            elif (d - data.date_local).days == 1:
                when = _tr("event_tomorrow", lang)
            else:
                when = f"{d.month}/{d.day}"
        else:
            when = str(d or "")
        origin = (
            _tr("event_system", lang)
            if e.get("source") == "system"
            else _tr("event_user", lang)
        )
        rows.append(f"  ・{when} {e.get('title', '')}（{origin}）")
    return _tr("events_title", lang) + "\n" + "\n".join(rows)


def _spend_section(data: BriefData, lang: str) -> Optional[str]:
    if not data.spend_yesterday:
        return None
    total = sum(float(r.get("total") or 0) for r in data.spend_yesterday)
    if total <= 0:
        return None
    parts = "、".join(
        f"{r.get('category') or 'other'} {fmt_money(r.get('total'))}"
        for r in data.spend_yesterday[:4]
    )
    lines = [
        _tr("spend_title", lang, amount=fmt_money(total), ccy=data.base_currency)
        + "："
        + parts
    ]
    if data.spend_mtd is not None:
        prev = (
            fmt_money(data.spend_prev_same_period)
            if data.spend_prev_same_period is not None
            else "-"
        )
        lines.append(
            "   "
            + _tr(
                "spend_mtd",
                lang,
                mtd=fmt_money(data.spend_mtd),
                prev=prev,
                ccy=data.base_currency,
            )
        )
    return "\n".join(lines)


def _watchlist_section(
    data: BriefData, lang: str, limit: int = MAX_WATCHLIST_ROWS
) -> Optional[str]:
    if not data.watchlist:
        return None
    rows = [
        f"  {w['symbol']:<8} {fmt_money(w.get('price'))}  {fmt_pct(w.get('change_pct'))}".rstrip()
        for w in data.watchlist[:limit]
    ]
    return _tr("watchlist_title", lang) + "\n" + "\n".join(rows)


def _calls_section(data: BriefData, lang: str) -> Optional[str]:
    """🎯 判斷結果：命中＝扣掉同市場基準後仍是正報酬（core/scorecard/rules.score）。"""
    if not data.scored_calls:
        return None
    rows = []
    for c in data.scored_calls[:MAX_CALL_ROWS]:
        direction = _tr(
            "call_bullish" if c.get("side") == "buy" else "call_bearish", lang
        )
        ret = f"{float(c.get('raw_pct') or 0):+.1f}%"
        bench = c.get("bench_symbol")
        if bench and c.get("bench_pct") is not None and c.get("excess_pct") is not None:
            ret += _tr(
                "call_vs_bench",
                lang,
                excess=f"{float(c['excess_pct']):+.1f}%",
                bench=str(bench).split(".")[0],
            )
        rows.append(
            "  ・"
            + _tr(
                "call_row",
                lang,
                symbol=c.get("symbol", ""),
                direction=direction,
                days=int(c.get("horizon_days") or 0),
                mark=_tr("call_hit" if c.get("hit") else "call_miss", lang),
                ret=ret,
            )
        )
    more = len(data.scored_calls) - MAX_CALL_ROWS
    if more > 0:
        rows.append("  " + _tr("calls_more", lang, n=more))
    return _tr("calls_title", lang) + "\n" + "\n".join(rows)


def _market_section(data: BriefData, lang: str) -> Optional[str]:
    """🌍 市場概況：只在早報沒有任何個人內容時用（新用戶第一天）。"""
    m = data.market_overview
    if not m:
        return None
    rows = [
        f"  {q['label']:<8} {fmt_money(q.get('price'))}  {fmt_pct(q.get('change_pct'))}".rstrip()
        for q in (m.get("quotes") or [])
    ]
    ev = m.get("next_event")
    if ev and isinstance(ev.get("date"), date):
        d = ev["date"]
        rows.append(
            "  "
            + _tr(
                "market_next_event",
                lang,
                date=f"{d.month}/{d.day}",
                title=ev.get("title", ""),
            )
        )
    if not rows:
        return None
    return (
        _tr("market_title", lang)
        + "\n"
        + "\n".join(rows)
        + "\n\n"
        + _tr("market_hint", lang)
    )


def _monthly_section(data: BriefData, lang: str) -> Optional[str]:
    m = data.monthly
    if not m:
        return None
    top = (
        "、".join(f"{c} {fmt_money(v)}" for c, v in (m.get("top_expense") or [])[:3])
        or "-"
    )
    prev = m.get("expense_prev_total")
    cur = m.get("expense_total")
    if prev and cur is not None and float(prev) > 0:
        delta = (float(cur) - float(prev)) / float(prev) * 100
        vs = f"{delta:+.0f}%"
    else:
        vs = "-"
    return (
        _tr("monthly_title", lang)
        + "\n  "
        + _tr(
            "monthly_body",
            lang,
            trades=int(m.get("trades") or 0),
            expense=fmt_money(cur),
            ccy=data.base_currency,
            top=top,
            vs=vs,
        )
    )


def compose_brief(data: BriefData) -> Optional[str]:
    lang = data.language or "zh-TW"
    name = data.display_name or ""
    header = _tr(
        "header", lang, date=f"{data.date_local.month}/{data.date_local.day}", name=name
    ).rstrip(" |｜")

    monthly = _monthly_section(data, lang)
    suggestion = _alert_suggestion_line(data, lang)
    # 結尾：哪段是 AI 寫的＋免責，再接 Telegram 回覆提示。放在截斷範圍外，超長也不會被切掉
    notes = []
    if data.insight:
        notes.append(_tr("ai_note", lang, model=data.insight_model or "AI"))
    if data.disclaimer:
        notes.append(_t("ui_messages.email_brief.disclaimer", lang))
    tail_parts = ["\n".join(notes)] if notes else []
    if data.telegram_hint:
        tail_parts.append(_tr("reply_hint", lang))
    tail = "".join("\n\n" + part for part in tail_parts)

    def assemble(position_rows: int, watch_rows: int) -> Optional[str]:
        # 自選是使用者自己挑的（2026-09-27）：跟持倉一樣是核心段落，每天都列
        core_sections = [
            _positions_section(data, lang, position_rows),
            _watchlist_section(data, lang, watch_rows),
            _calls_section(data, lang),
            _alerts_section(data, lang),
            _events_section(data, lang),
            _spend_section(data, lang),
        ]
        sections = [s for s in core_sections if s]
        if not sections:
            # 沒有個人內容：用市場概況；沒有才不送
            fallback = _market_section(data, lang)
            if not fallback:
                return None
            sections.append(fallback)
        if monthly:
            sections.append(monthly)
        if data.insight:
            sections.append(_tr("insight_prefix", lang) + data.insight.strip())
        if suggestion:
            sections.append(suggestion)
        return header + "\n\n" + "\n\n".join(sections)

    body = assemble(MAX_POSITION_ROWS, MAX_WATCHLIST_ROWS)
    if body is None:
        return None
    # 超長：持倉與自選各縮到 3 列
    if len(body) + len(tail) > MAX_CHARS and (
        len(data.positions) > 3 or len(data.watchlist) > 3
    ):
        body = assemble(3, 3)
    if len(body) + len(tail) > MAX_CHARS:
        body = body[: MAX_CHARS - len(tail) - 1].rstrip() + "…"
    return body + tail
