"""Wallet Monitor API — 錢包監測 dashboard 後端

    - GET  /api/wallet-monitor/overview   → 持倉總覽 + 風險 + 信任分數
    - GET  /api/wallet-monitor/events     → 最近 events（轉入/轉出時間軸）
    - GET  /api/wallet-monitor/settings   → 讀警示設定
    - PUT  /api/wallet-monitor/settings   → 存警示設定
    - POST /api/wallet-monitor/wallets    → 添加監測錢包
    - DELETE /api/wallet-monitor/wallets/{address} → 移除監測錢包

對應 design doc：docs/plans/2026-08-08-wallet-monitor-dashboard-design.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger as api_logger
from api.utils import run_sync
from core.config import WALLET_MONITOR_ENABLED, WALLET_MONITOR_PREMIUM_GATE_ENABLED
from core.database.user import (
    get_wallet_alert_settings,
    save_wallet_alert_settings,
)
from core.entitlement import (
    Entitlement,
    entitlement_summary,
    resolve_entitlement_for_user,
)
from core.identity.scoring import get_user_trust_tier
from core.onchain.addresses import is_evm_address, is_ton_address, ton_friendly_to_raw
from core.wallet_monitor.engine import fetch_events

router = APIRouter(prefix="/api/wallet-monitor", tags=["Wallet Monitor"])
_logger = logging.getLogger(__name__)

# 預設警示設定（v1）。
DEFAULT_ALERT_SETTINGS: Dict[str, Any] = {
    "monitored_wallets": [],
    "alerts": {
        "incoming": {"enabled": False, "min_amount_ton": 10},
        "outgoing": {"enabled": False, "min_amount_ton": 10},
        "scam": {"enabled": True},
        "large_out": {"enabled": False, "threshold_ton": 500},
        "custom": [],
    },
    "channels": {"in_app": True, "telegram": True, "discord": False},
}


def _disabled() -> None:
    raise HTTPException(status_code=503, detail="Wallet monitor is disabled")


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any] = Field(..., description="完整警示設定（會整包覆蓋）")


class AddWalletRequest(BaseModel):
    address: str = Field(..., min_length=10, max_length=80)
    label: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=60)
    chain: str = Field(default="ton", max_length=20)  # 路線 C: ton/eth/bsc/polygon/...


# monitored_wallets 升級:舊格式是純字串陣列 ["EQ..."],新格式是物件陣列
# [{address, label, reason, chain}]。_normalize_wallet_entry 把兩者統一成物件;
# _wallet_addresses 把整個陣列壓平成純地址字串(給 cron / engine 用)。
def _normalize_wallet_entry(w: Any) -> Dict[str, str]:
    """把舊(純字串)或新({address,label,reason,chain})格式的單筆統一成物件。"""
    if isinstance(w, str):
        return {"address": w, "label": "", "reason": "", "chain": "ton"}
    if isinstance(w, dict) and "address" in w:
        return {
            "address": str(w.get("address", "")),
            "label": str(w.get("label", "")),
            "reason": str(w.get("reason", "")),
            "chain": str(w.get("chain", "ton")),
        }
    return {"address": str(w), "label": "", "reason": "", "chain": "ton"}


def _wallet_key(address: str) -> str:
    """比對用：TON 的各種寫法（EQ／UQ／raw…）是同一個錢包，一律轉 raw；
    EVM 的 checksum 只是大小寫，一律小寫。"""
    a = (address or "").strip()
    if is_evm_address(a):
        return a.lower()
    return ton_friendly_to_raw(a) or a


def _own_wallet(user_id: str) -> str:
    """登入身份對應的錢包：TON 用戶 user_id 即地址；EVM 用戶是 evm_<小寫地址>。"""
    uid = str(user_id or "")
    return uid[4:] if uid.startswith("evm_") else uid


def _is_own_wallet(address: str, user_id: str) -> bool:
    """address 是不是登入者自己的錢包（任何寫法都算；以前字串比對，UQ 查 EQ 身份被擋）。

    只認合法的 TON／EVM 地址：tg_… 之類的非錢包身份把自己的 user_id 填進 address
    不能算「自己的錢包」而繞過 premium gate。
    """
    key = _wallet_key(address)
    if not (is_ton_address(key) or is_evm_address(key)):
        return False
    return key == _wallet_key(_own_wallet(user_id))


def _wallet_addresses(wallets: list) -> list[str]:
    """壓平成純地址字串陣列(向後相容 cron / engine / overview 餘額查詢)。"""
    return [_normalize_wallet_entry(w)["address"] for w in wallets]


# 單一使用者最多監測的錢包數(防濫用 + 控制 TonAPI 呼叫量)。
MAX_MONITORED_WALLETS = 5


# ── Entitlement gate 輔助（design 2026-08-13 §9.4 / §9.7）───────────────────
async def _current_entitlement(user_id: str) -> Entitlement:
    """解析當前使用者權益（sync DB 包 run_sync）。"""
    return await run_sync(resolve_entitlement_for_user, user_id)


def _settings_enables_scheduled_alerts(settings: dict) -> bool:
    """True = 設定啟用了任何背景推送警示規則（incoming/outgoing/large_out/scam）。

    這些屬於 Premium 排程監測能力（design §5.2）。
    """
    alerts = settings.get("alerts") if isinstance(settings, dict) else None
    if not isinstance(alerts, dict):
        return False
    for key in ("incoming", "outgoing", "large_out"):
        rule = alerts.get(key)
        if isinstance(rule, dict) and rule.get("enabled"):
            return True
    scam = alerts.get("scam")
    if isinstance(scam, dict) and scam.get("enabled"):
        return True
    return False


def _require_scheduled_monitoring(action: str, ent: Entitlement) -> None:
    """gate 開啟時，非 active premium 嘗試排程監測 → 403 + upgrade metadata。"""
    raise HTTPException(
        status_code=403,
        detail={
            "upgrade_required": True,
            "action": action,
            "current_tier": ent.tier,
            "reason": "premium_required_for_scheduled_monitoring",
        },
    )


@router.get("/overview")
@limiter.limit("30/minute")
async def wallet_overview(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """持倉總覽：餘額 + jetton + 風險 + 信任分數。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]

    settings = await run_sync(get_wallet_alert_settings, user_id) or DEFAULT_ALERT_SETTINGS
    # 正規化（舊純字串 → 物件）+ 壓平成地址給餘額查詢
    wallets = [_normalize_wallet_entry(w) for w in settings.get("monitored_wallets", [])]
    if not wallets:
        # v1 預設監測登入錢包（TON 用戶 user_id 即錢包地址）
        if is_ton_address(user_id):
            wallets = [_normalize_wallet_entry(user_id)]

    addr_list = [w["address"] for w in wallets]

    # Entitlement gate（§9.4）：開啟時 Free 僅查 wallet_snapshot_count 個錢包。
    ent = await _current_entitlement(user_id)
    if WALLET_MONITOR_PREMIUM_GATE_ENABLED and not ent.can_use_scheduled_monitoring:
        wallets = wallets[: ent.wallet_snapshot_count]
        addr_list = [w["address"] for w in wallets]

    # 抓每個錢包的餘額（並行）
    balances = []
    for i, addr in enumerate(addr_list):
        chain = wallets[i].get("chain", "ton")
        bal, symbol = await _fetch_balance(addr, chain)
        if bal is not None:
            entry = {"address": addr, "balance_ton": bal, "symbol": symbol, "chain": chain}
            # 帶上 label/reason 供前端顯示暱稱與監測原因
            if wallets[i]["label"]:
                entry["label"] = wallets[i]["label"]
            if wallets[i]["reason"]:
                entry["reason"] = wallets[i]["reason"]
            balances.append(entry)

    return {
        "success": True,
        "wallets": balances,
        "trust": {"tier": get_user_trust_tier(user_id)},
        "alert_settings": settings,
        "entitlement": entitlement_summary(ent),
        # 前端據此判斷「升級解鎖」橫幅只在限制確實生效時顯示（gate 開啟 + Free）。
        "monitoring_gate_enabled": WALLET_MONITOR_PREMIUM_GATE_ENABLED,
    }


@router.get("/events")
@limiter.limit("30/minute")
async def wallet_events(
    request: Request,
    current_user: dict = Depends(get_current_user),
    address: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    """最近 events（轉入/轉出時間軸）。address 缺省時用登入錢包。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]
    addr = address or _own_wallet(user_id)

    # Entitlement gate（§9.4）：開啟時 Free 僅能查自己的登入錢包快照，查他人 → 403。
    if WALLET_MONITOR_PREMIUM_GATE_ENABLED:
        ent = await _current_entitlement(user_id)
        if not ent.can_use_scheduled_monitoring and not _is_own_wallet(addr, user_id):
            _require_scheduled_monitoring("view_events", ent)

    raw_events = await fetch_events(addr)
    # 轉成前端可顯示的簡化格式(含 TonTransfer + JettonTransfer + comment)
    simplified = []
    for ev in raw_events[:limit]:
        actions = []
        for action in ev.get("actions", []) or []:
            atype = action.get("type", "")
            if atype == "TonTransfer":
                tt = action.get("TonTransfer", {})
                actions.append({
                    "type": "ton",
                    "amount_ton": round(int(tt.get("amount", 0) or 0) / 1e9, 4),
                    "sender": (tt.get("sender", {}) or {}).get("address", ""),
                    "recipient": (tt.get("recipient", {}) or {}).get("address", ""),
                    "comment": tt.get("comment", ""),
                })
            elif atype == "JettonTransfer":
                jt = action.get("JettonTransfer", {})
                jetton = jt.get("jetton", {}) or {}
                decimals = int(jetton.get("decimals", 9) or 9)
                actions.append({
                    "type": "jetton",
                    "amount": round(int(jt.get("amount", 0) or 0) / (10**decimals), 4),
                    "symbol": jetton.get("symbol", ""),
                    "name": jetton.get("name", ""),
                    "sender": (jt.get("sender", {}) or {}).get("address", ""),
                    "recipient": (jt.get("recipient", {}) or {}).get("address", ""),
                    "comment": jt.get("comment", ""),
                })
        simplified.append({
            "event_id": ev.get("event_id", ""),
            "timestamp": ev.get("timestamp", 0),
            "is_scam": bool(ev.get("is_scam", False)),
            "actions": actions,
        })
    return {"success": True, "address": addr, "events": simplified}


@router.get("/settings")
@limiter.limit("30/minute")
async def get_settings(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """讀用戶警示設定（無則回預設）。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]
    settings = await run_sync(get_wallet_alert_settings, user_id) or DEFAULT_ALERT_SETTINGS
    ent = await _current_entitlement(user_id)
    return {"success": True, "settings": settings, "entitlement": entitlement_summary(ent)}


@router.put("/settings")
@limiter.limit("20/minute")
async def update_settings(
    request: Request,
    body: SettingsUpdate,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """存用戶警示設定（整包覆蓋，前端送完整結構）。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]

    # 基本校驗：必須是 dict 且含 alerts 結構
    settings = body.settings
    if not isinstance(settings.get("alerts"), dict):
        raise HTTPException(status_code=400, detail="Invalid settings: missing alerts")

    # Entitlement gate（§9.4）：開啟時 Free 啟用排程警示 → 403 upgrade。
    if WALLET_MONITOR_PREMIUM_GATE_ENABLED and _settings_enables_scheduled_alerts(settings):
        ent = await _current_entitlement(user_id)
        if not ent.can_use_scheduled_monitoring:
            _require_scheduled_monitoring("enable_alerts", ent)

    ok, reason = await run_sync(save_wallet_alert_settings, user_id, settings)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {reason}")
    api_logger.info("[wallet-monitor] user %s updated alert settings", user_id)
    return {"success": True, "settings": settings}


@router.post("/wallets")
@limiter.limit("10/minute")
async def add_wallet(
    request: Request,
    body: AddWalletRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """添加監測錢包（可加 label 暱稱 + reason 監測原因）。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]
    addr = (body.address or "").strip()
    chain = (body.chain or "ton").strip().lower()
    # 鏈感知地址驗證（路線 C）：依 chain 分派
    from core.wallet_monitor.adapters import get_adapter

    adapter = get_adapter(chain)
    if adapter and not adapter.is_valid_address(addr):
        raise HTTPException(status_code=400, detail=f"Invalid address for chain '{chain}'")
    elif not adapter:
        # 未知 chain → 回退到 TON 驗證（安全預設）
        if not is_ton_address(addr):
            raise HTTPException(status_code=400, detail=f"Unsupported chain '{chain}'")

    # Entitlement gate（§9.4）：開啟時新增監測錢包 = 排程監測，需 active premium。
    if WALLET_MONITOR_PREMIUM_GATE_ENABLED:
        ent = await _current_entitlement(user_id)
        if not ent.can_use_scheduled_monitoring:
            _require_scheduled_monitoring("add_wallet", ent)

    settings = await run_sync(get_wallet_alert_settings, user_id) or dict(DEFAULT_ALERT_SETTINGS)
    # 先正規化舊格式（純字串 → 物件），確保比對與儲存格式一致
    wallets = [_normalize_wallet_entry(w) for w in settings.get("monitored_wallets", [])]
    existing_keys = [_wallet_key(w["address"]) for w in wallets]
    if _wallet_key(addr) in existing_keys:
        return {"success": True, "message": "already_monitored", "settings": settings}
    if len(wallets) >= MAX_MONITORED_WALLETS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_MONITORED_WALLETS} monitored wallets allowed",
        )
    wallets.append({
        "address": addr,
        "label": (body.label or "").strip()[:40],
        "reason": (body.reason or "").strip()[:60],
        "chain": chain,
    })
    settings["monitored_wallets"] = wallets

    ok, _ = await run_sync(save_wallet_alert_settings, user_id, settings)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save settings")
    api_logger.info("[wallet-monitor] user %s added wallet %s", user_id, addr)
    return {"success": True, "settings": settings}


@router.delete("/wallets/{address}")
@limiter.limit("10/minute")
async def remove_wallet(
    request: Request,
    address: str,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """移除監測錢包。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()
    user_id = current_user["user_id"]
    settings = await run_sync(get_wallet_alert_settings, user_id) or dict(DEFAULT_ALERT_SETTINGS)
    wallets = [_normalize_wallet_entry(w) for w in settings.get("monitored_wallets", [])]
    key = _wallet_key(address)
    new_wallets = [w for w in wallets if _wallet_key(w["address"]) != key]
    if len(new_wallets) != len(wallets):
        settings["monitored_wallets"] = new_wallets
        ok, _ = await run_sync(save_wallet_alert_settings, user_id, settings)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to save settings")
    return {"success": True, "settings": settings}


async def _fetch_balance(address: str, chain: str = "ton") -> tuple[float | None, str]:
    """抓單一錢包的原生代幣餘額（鏈感知，路線 C）。回傳 (amount, symbol)。"""
    from core.wallet_monitor.adapters import get_adapter

    adapter = get_adapter(chain)
    if not adapter:
        # 回退到 TON（安全預設）
        adapter = get_adapter("ton")
    if not adapter:
        return None, "?"

    try:
        result = await adapter.fetch_balance(address)
        if result:
            return result.amount, result.symbol
        return None, adapter.native_symbol
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.warning("[wallet-monitor] balance fetch failed %s: %s", address, exc)
        return None, adapter.native_symbol


@router.get("/wallet/{address}/detail")
@limiter.limit("30/minute")
async def wallet_detail(
    request: Request,
    address: str,
    current_user: dict = Depends(get_current_user),
    limit: int = 30,
) -> Dict[str, Any]:
    """單一錢包明細:TON 餘額 + jetton 持倉 + USD 估值 + 流入流出統計 + 活動時間軸。"""
    if not WALLET_MONITOR_ENABLED:
        _disabled()

    # 基本資訊(用 label/reason 找到使用者設定的暱稱)
    user_id = current_user["user_id"]

    # Entitlement gate（§9.4）：開啟時 Free 僅能查自己的登入錢包快照，查他人 → 403。
    if WALLET_MONITOR_PREMIUM_GATE_ENABLED:
        ent = await _current_entitlement(user_id)
        if not ent.can_use_scheduled_monitoring and not _is_own_wallet(
            address, user_id
        ):
            _require_scheduled_monitoring("view_wallet_detail", ent)

    settings = await run_sync(get_wallet_alert_settings, user_id) or DEFAULT_ALERT_SETTINGS
    wallets = [_normalize_wallet_entry(w) for w in settings.get("monitored_wallets", [])]
    # 換個寫法（EQ↔UQ、EVM 大小寫）也要找得到 label 與 chain——找不到 chain 會預設 ton
    key = _wallet_key(address)
    wallet_meta = next(
        (w for w in wallets if _wallet_key(w["address"]) == key),
        {"address": address, "label": "", "reason": ""},
    )

    # 原生代幣餘額（鏈感知，路線 C）
    chain = wallet_meta.get("chain", "ton")
    native_balance, native_symbol = await _fetch_balance(address, chain)

    # Jetton 持倉(呼叫已存在的 tool)
    jettons = []
    usd_total = None
    ton_usd_price = None
    try:
        from core.tools.crypto_modules.ton_jetton_balances import (
            get_ton_jetton_balances,
        )

        result = await get_ton_jetton_balances.ainvoke({"address": address})
        if result and not result.get("error"):
            jettons = result.get("jettons", [])[:20]  # 最多 20 個
            usd_total = result.get("usd_total")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.warning("[wallet-monitor] jetton balances failed %s: %s", address, exc)

    # TON USD 估值（僅 TON 鏈；EVM 鏈的 USD 估值 Phase 2）
    if native_balance is not None and chain == "ton":
        try:
            from core.tools.crypto_modules.ton_price import get_ton_usd_price

            ton_usd_price = await run_sync(get_ton_usd_price)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001
            pass

    ton_usd_value = round(native_balance * ton_usd_price, 2) if (native_balance and ton_usd_price and chain == "ton") else None

    # 活動 events(含 jetton 轉帳)
    raw_events = await fetch_events(address)
    # TonAPI 事件的地址是 raw，這裡的 address 多半是 friendly——轉 raw 才比得起來
    me = _wallet_key(address)
    events = []
    inflow_ton = 0.0
    outflow_ton = 0.0
    inflow_count = 0
    outflow_count = 0
    for ev in raw_events[:limit]:
        ev_actions = []
        for action in ev.get("actions", []) or []:
            atype = action.get("type", "")
            if atype == "TonTransfer":
                tt = action.get("TonTransfer", {})
                amount_ton = round(int(tt.get("amount", 0) or 0) / 1e9, 4)
                sender = (tt.get("sender", {}) or {}).get("address", "")
                recipient = (tt.get("recipient", {}) or {}).get("address", "")
                is_in = _wallet_key(recipient) == me
                if is_in:
                    inflow_ton += amount_ton
                    inflow_count += 1
                else:
                    outflow_ton += amount_ton
                    outflow_count += 1
                ev_actions.append({
                    "type": "ton", "amount_ton": amount_ton,
                    "sender": sender, "recipient": recipient,
                    "comment": tt.get("comment", ""),
                    "is_in": is_in,
                })
            elif atype == "JettonTransfer":
                jt = action.get("JettonTransfer", {})
                jetton = jt.get("jetton", {}) or {}
                decimals = int(jetton.get("decimals", 9) or 9)
                ev_actions.append({
                    "type": "jetton",
                    "amount": round(int(jt.get("amount", 0) or 0) / (10**decimals), 4),
                    "symbol": jetton.get("symbol", ""),
                    "name": jetton.get("name", ""),
                    "sender": (jt.get("sender", {}) or {}).get("address", ""),
                    "recipient": (jt.get("recipient", {}) or {}).get("address", ""),
                    "comment": jt.get("comment", ""),
                    "is_in": _wallet_key((jt.get("recipient", {}) or {}).get("address", "")) == me,
                })
        if ev_actions:
            events.append({
                "timestamp": ev.get("timestamp", 0),
                "is_scam": bool(ev.get("is_scam", False)),
                "actions": ev_actions,
            })

    return {
        "success": True,
        "address": address,
        "label": wallet_meta.get("label", ""),
        "reason": wallet_meta.get("reason", ""),
        "ton_balance": native_balance,  # 欄位名保留向後相容；值是原生代幣量
        "native_symbol": native_symbol,
        "chain": chain,
        "ton_usd_value": ton_usd_value,
        "jettons": jettons,
        "usd_total": usd_total,
        "inflow_ton": round(inflow_ton, 4),
        "outflow_ton": round(outflow_ton, 4),
        "inflow_count": inflow_count,
        "outflow_count": outflow_count,
        "net_flow_ton": round(inflow_ton - outflow_ton, 4),
        "events": events,
    }


__all__ = ["router", "DEFAULT_ALERT_SETTINGS"]
