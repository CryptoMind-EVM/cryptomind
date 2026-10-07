"""
錢包監測引擎——抓取 TonAPI events → 比對用戶警示規則 → 產生 AlertEvent

分層（對應 design doc）：
    fetch_events()        → TonAPI /v2/accounts/{addr}/events（實測驗證可用）
         │
         ▼
    match_rules()         → 比對用戶設定的警示規則（轉入/轉出/詐騙/大額）
         │
         ▼
    AlertEvent            → 統一事件結構，送給 AlertDispatcher（channels.py）

設計取捨：
    - 純函式、無 side effect（發送在 channels.py）——可獨立測試。
    - graceful：任何 API 失敗不中斷，回空事件列表。
    - 上次已處理的 event 用 event_id 去重（Redis 快取 last_processed_event_id，
      按用戶分開——同一個錢包被兩個人監測，各自的進度互不影響）。
      沒有游標＝剛加入監測：第一次輪詢只記基準、不警示（見 match_rules）。
    - 排程輪詢（fetch_new_events）往舊翻頁直到碰到上次處理到的那筆——兩次輪詢
      之間超過一頁也不漏；翻頁有上限，超過記 warning。
    - TonAPI 事件裡的地址是 raw（0:hex），監測地址多半是 friendly（EQ／UQ…）：
      比對一律先轉 raw（core.onchain.addresses）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from core.onchain.addresses import is_ton_address, ton_friendly_to_raw
from core.shared_cache import get_json, set_json

logger = logging.getLogger(__name__)

_TONAPI_EVENTS_URL = "https://tonapi.io/v2/accounts/{address}/events"
# 每頁筆數。
_MAX_EVENTS_PER_FETCH = 20
# 排程輪詢往舊翻頁的上限（頁）——防高頻錢包一次翻爆 TonAPI、也防游標失效時洪水式警示。
_MAX_EVENT_PAGES = 5
# 去重快取 key + TTL（事件抓過就不重複警示，TTL = 輪詢間隔 × 3 保險）。
# 按 (用戶, 地址) 分開：同一個錢包被兩個人監測時，只按地址存的話先輪到的人把游標
# 推到最新，另一個人就再也收不到那些事件的警示。
_LAST_EVENT_KEY = "wallet_monitor:user_last_event:{user_id}:{address}"
# 舊 key（只按地址）——只在上線那一輪讀，見 _last_processed_event_id。
_LEGACY_LAST_EVENT_KEY = "wallet_monitor:last_event:{address}"
# 每輪輪詢都會重寫游標，TTL 只在停機時起作用：過期後下一輪會變 baseline（不發警示），
# 所以要撐過一般停機；恢復後靠翻頁（最多 5 頁）補抓停機期間的事件
_LAST_EVENT_TTL = 7 * 24 * 3600


@dataclass
class AlertEvent:
    """統一警示事件（送給 AlertDispatcher 的標準格式）。"""

    wallet_address: str
    event_id: str
    event_type: str  # "incoming" / "outgoing" / "scam_contact" / "large_out"
    amount_ton: float
    counterparty: str  # 對端地址
    is_scam: bool
    timestamp: int
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "amount_ton": self.amount_ton,
            "counterparty": self.counterparty,
            "is_scam": self.is_scam,
            "timestamp": self.timestamp,
        }


async def fetch_events(
    address: str, *, since_event_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """抓取錢包最近的 events（TonAPI，新的在前）。

    since_event_id 沒給：只抓最新一頁（頁面顯示、第一次輪詢）。
    有給：用 before_lt 往舊翻頁，直到碰到它（不含它與更舊的），最多 _MAX_EVENT_PAGES 頁。
    第一頁失敗回空；後面的頁失敗跟碰到翻頁上限一樣——已抓到的較新事件照常回傳、
    記 warning（回空等下一輪的話，TonAPI 第二頁一直 429 時這個錢包會整個停擺）。

    Returns:
        list of raw event dicts（含 actions、timestamp、is_scam）。失敗回空。
    """
    addr = (address or "").strip()
    if not is_ton_address(addr):
        return []

    events: List[Dict[str, Any]] = []
    before_lt: Optional[int] = None
    stopped = f"page cap ({_MAX_EVENT_PAGES} pages)"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for page in range(_MAX_EVENT_PAGES):
                params: Dict[str, Any] = {"limit": _MAX_EVENTS_PER_FETCH}
                if before_lt:
                    params["before_lt"] = before_lt
                resp = await client.get(
                    _TONAPI_EVENTS_URL.format(address=addr), params=params
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[wallet_monitor] events %s page %d -> HTTP %s",
                        addr, page + 1, resp.status_code,
                    )
                    if not events:
                        return []
                    stopped = f"page {page + 1} HTTP {resp.status_code}"
                    break
                body = resp.json()
                page_events = body.get("events", []) or []
                if not since_event_id:
                    return page_events
                for ev in page_events:
                    if ev.get("event_id") == since_event_id:
                        return events
                    events.append(ev)
                before_lt = body.get("next_from")
                if not page_events or not before_lt:
                    return events
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("[wallet_monitor] fetch events failed %s: %s", addr, exc)
        if not events:
            return []
        stopped = type(exc).__name__

    logger.warning(
        "[wallet_monitor] %s: stopped at %s before last seen event %s; "
        "processing %d newer events, older events skipped",
        addr, stopped, since_event_id, len(events),
    )
    return events


def _last_processed_event_id(user_id: str, address: str) -> Optional[str]:
    """這個用戶上次處理到的 event_id。

    per-user 游標還沒有（剛上線）就讀舊的 per-address 游標當起點：沒有起點會走第一次
    輪詢的行為（最新一頁全當新事件），所有既有監測會同時重發一整頁警示。match_rules
    會把它寫進 per-user 游標，下一輪起就不再讀舊的；舊 key 沒人再寫，TTL 到就消失。
    """
    last = get_json(_LAST_EVENT_KEY.format(user_id=user_id, address=address))
    if last is None:
        last = get_json(_LEGACY_LAST_EVENT_KEY.format(address=address))
    return last


async def fetch_new_events(address: str, *, user_id: str) -> List[Dict[str, Any]]:
    """排程輪詢用：這個用戶上次處理到的那筆之後的所有 events（見 fetch_events）。"""
    return await fetch_events(
        address, since_event_id=_last_processed_event_id(user_id, address)
    )


def _mark_processed(user_id: str, address: str, event_id: str) -> None:
    set_json(
        _LAST_EVENT_KEY.format(user_id=user_id, address=address),
        event_id,
        _LAST_EVENT_TTL,
    )


def _extract_transfer(action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """從 action 取出轉帳資訊(支援 TonTransfer + JettonTransfer)。

    回傳 dict 統一格式:
      type: "ton" | "jetton"
      amount: 原始整數(nanoTON 或 jetton 最小單位)
      sender / recipient: 地址
      comment: 留言(僅 TonTransfer 有)
      symbol / name / decimals: 僅 jetton
    """
    atype = action.get("type", "")

    if atype == "TonTransfer":
        tt = action.get("TonTransfer", {})
        return {
            "type": "ton",
            "amount": tt.get("amount", 0),  # nanoTON
            "sender": (tt.get("sender", {}) or {}).get("address", ""),
            "recipient": (tt.get("recipient", {}) or {}).get("address", ""),
            "comment": tt.get("comment", ""),
        }

    if atype == "JettonTransfer":
        jt = action.get("JettonTransfer", {})
        jetton = jt.get("jetton", {}) or {}
        return {
            "type": "jetton",
            "amount": jt.get("amount", 0),  # jetton 最小單位
            "sender": (jt.get("sender", {}) or {}).get("address", ""),
            "recipient": (jt.get("recipient", {}) or {}).get("address", ""),
            "comment": jt.get("comment", ""),
            "symbol": jetton.get("symbol", ""),
            "name": jetton.get("name", ""),
            "decimals": jetton.get("decimals", 9),
        }

    return None


def match_rules(
    address: str,
    events: List[Dict[str, Any]],
    settings: Dict[str, Any],
    *,
    user_id: str,
) -> List[AlertEvent]:
    """比對 events 與用戶警示規則，產生 AlertEvent 列表。

    Args:
        address: 被監測的錢包地址。
        events: fetch_events 的 raw events。
        settings: 用戶的警示設定（JSONB 結構，見 migration c018）。
        user_id: 監測的用戶——去重游標按 (用戶, 地址) 分開存。

    Returns:
        符合規則的 AlertEvent 列表（新的在前）。已處理的去重。
    """
    alerts_cfg = (settings or {}).get("alerts", {})
    incoming_cfg = alerts_cfg.get("incoming", {})
    outgoing_cfg = alerts_cfg.get("outgoing", {})
    scam_cfg = alerts_cfg.get("scam", {})
    large_out_cfg = alerts_cfg.get("large_out", {})

    matches: List[AlertEvent] = []
    processed_any = False
    last_id = _last_processed_event_id(user_id, address)
    if last_id is None:
        # 第一次輪詢（剛加入監測、也沒有舊的 per-address 游標）：只記基準、不警示——
        # 不然最新一頁（最多 20 筆舊事件）會全被當成新事件發出去。加入後到第一次輪詢
        # 之間真的發生的轉帳會併進基準（可接受）。抓不到事件（TonAPI 失敗或空錢包）
        # 就先不記：記成「空」的話下一輪沒東西可比，整頁舊事件又會全被當新的。
        baseline = next((e["event_id"] for e in events if e.get("event_id")), None)
        if baseline:
            _mark_processed(user_id, address, baseline)
            logger.info(
                "[wallet_monitor] %s: first poll for user %s, baseline %s (no alerts)",
                address,
                user_id,
                baseline,
            )
        return []
    # TonAPI 給 raw、監測地址多半是 friendly——同一個錢包，比對前都轉 raw
    me = ton_friendly_to_raw(address) or address

    for ev in events:
        event_id = ev.get("event_id", "")
        if not event_id:
            continue
        # 去重：碰到已處理的最新 event_id 就停止（events 新的在前）
        if last_id and event_id == last_id:
            break
        processed_any = True

        ts = ev.get("timestamp", 0)
        is_scam = bool(ev.get("is_scam"))

        for action in ev.get("actions", []) or []:
            transfer = _extract_transfer(action)
            if not transfer:
                continue
            sender = transfer["sender"]
            recipient = transfer["recipient"]
            is_in = (ton_friendly_to_raw(recipient) or recipient) == me
            is_out = (ton_friendly_to_raw(sender) or sender) == me

            # 詐騙往來（任何一方是 scam 標記）— jetton 也算
            if is_scam and scam_cfg.get("enabled", True):
                matches.append(AlertEvent(
                    wallet_address=address, event_id=event_id,
                    event_type="scam_contact", amount_ton=0.0,
                    counterparty=sender if is_in else recipient,
                    is_scam=True, timestamp=ts,
                ))

            # TON 金額規則只適用 TonTransfer(jetton 的 amount 不是 nanoTON)
            if transfer.get("type") != "ton":
                continue

            amount_nano = int(transfer["amount"] or 0)
            amount_ton = amount_nano / 1e9

            if amount_ton <= 0:
                continue

            # 轉入（別人 → 被監測錢包）
            if is_in and incoming_cfg.get("enabled"):
                if amount_ton >= float(incoming_cfg.get("min_amount_ton", 0)):
                    matches.append(AlertEvent(
                        wallet_address=address, event_id=event_id,
                        event_type="incoming", amount_ton=amount_ton,
                        counterparty=sender, is_scam=is_scam, timestamp=ts,
                    ))

            # 轉出（被監測錢包 → 別人）
            if is_out and outgoing_cfg.get("enabled"):
                if amount_ton >= float(outgoing_cfg.get("min_amount_ton", 0)):
                    matches.append(AlertEvent(
                        wallet_address=address, event_id=event_id,
                        event_type="outgoing", amount_ton=amount_ton,
                        counterparty=recipient, is_scam=is_scam, timestamp=ts,
                    ))

            # 大額轉出（閾值）
            if is_out and large_out_cfg.get("enabled"):
                if amount_ton >= float(large_out_cfg.get("threshold_ton", 500)):
                    matches.append(AlertEvent(
                        wallet_address=address, event_id=event_id,
                        event_type="large_out", amount_ton=amount_ton,
                        counterparty=recipient, is_scam=is_scam, timestamp=ts,
                    ))


    # 游標每輪都寫：有新事件＝推到最新一筆；沒有＝原值重寫刷新 TTL。只在有新事件時
    # 寫的話，安靜超過 TTL 的錢包游標會過期，下一輪把最新一頁全當新事件重發。
    newest = events[0].get("event_id", "") if (processed_any and events) else ""
    if newest or last_id:
        _mark_processed(user_id, address, newest or last_id)

    return matches


__all__ = ["AlertEvent", "fetch_events", "fetch_new_events", "match_rules"]
