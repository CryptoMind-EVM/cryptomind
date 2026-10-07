"""
GoPlus Security 工具 — 代幣與地址安全檢測

兩個工具：
1. check_token_security：代幣合約風險檢測（免費、免 API key）
2. check_address_safety：錢包地址惡意行為檢測（平台官方 key，自動換 token）

API 來源：https://api.gopluslabs.io
- Token Security API：完全免費、免 auth、回應 ~0.1 秒、11+ 鏈支援。
- Address Security API：需 Bearer token。平台用 GOPLUS_APP_KEY + GOPLUS_APP_SECRET
  透過 POST /api/v1/token（SHA1 簽章）換 access_token，token 帶快取（到期前重用）。
"""

import asyncio
import hashlib
import logging
import os
import time

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# GoPlus API base
_GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"

# 常用 chain_id → 名稱（給使用者看的人類可讀標籤）
_CHAIN_NAMES = {
    "1": "Ethereum",
    "56": "BSC",
    "42161": "Arbitrum",
    "137": "Polygon",
    "10": "Optimism",
    "8453": "Base",
    "43114": "Avalanche",
    "250": "Fantom",
    "324": "zkSync Era",
    "534352": "Scroll",
    "59144": "Linea",
}


def _chain_label(chain_id: str) -> str:
    return _CHAIN_NAMES.get(str(chain_id), f"Chain {chain_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Token 管理：App Key + App Secret → access_token（SHA1 簽章，帶快取）
# ─────────────────────────────────────────────────────────────────────────────

# 模組層級快取：避免每次查詢都重換 token（GoPlus token 有效期 ~7200 秒）
_cached_token: str | None = None
_cached_token_expires: float = 0.0
# 提前 60 秒過期，避免邊界競態
_TOKEN_REFRESH_MARGIN = 60


def _get_goplus_token() -> str | None:
    """用 GOPLUS_APP_KEY + GOPLUS_APP_SECRET 換 access_token（帶快取）。

    GoPlus 認證流程：
    1. sign = sha1(app_key + time + app_secret)
    2. POST /api/v1/token {app_key, sign, time}
    3. 回傳 access_token（有效期 ~7200 秒）+ expires_in

    快取：token 到期前重用，不自動過期才重換。
    回傳 None 表示未設定官方 key（address security 功能不可用）。
    """
    global _cached_token, _cached_token_expires

    # 快取命中（尚未過期）
    if _cached_token and time.time() < _cached_token_expires:
        return _cached_token

    app_key = os.getenv("GOPLUS_APP_KEY", "").strip()
    app_secret = os.getenv("GOPLUS_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        return None

    t = int(time.time())
    sign = hashlib.sha1(f"{app_key}{t}{app_secret}".encode()).hexdigest()

    try:
        resp = httpx.post(
            f"{_GOPLUS_BASE}/token",
            json={"app_key": app_key, "sign": sign, "time": t},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("[GoPlus] token exchange failed: %s", e)
        return None

    if data.get("code") != 1:
        logger.error(
            "[GoPlus] token exchange error: %s (code=%s)",
            data.get("message"),
            data.get("code"),
        )
        return None

    result = data.get("result", {})
    _cached_token = result.get("access_token")
    expires_in = result.get("expires_in", 7200)
    _cached_token_expires = time.time() + expires_in - _TOKEN_REFRESH_MARGIN
    logger.debug("[GoPlus] token refreshed, expires in %ds", expires_in)
    return _cached_token


# ─────────────────────────────────────────────────────────────────────────────
# Token Security（免費、免 key）
# ─────────────────────────────────────────────────────────────────────────────


@tool
def check_token_security(contract_address: str, chain_id: int = 1) -> str:
    """檢測代幣合約的安全風險（蜜罐、rug pull 前兆、隱藏權限等）。

    貼上代幣合約地址即可獲得完整安全報告，包含：
    - 蜂蜜罐風險（is_honeypot：能買不能賣）
    - 可無限鑄造 + owner 可改餘額（rug pull 前兆）
    - 合約是否開源、是否有隱藏 owner
    - 買賣稅率、滑點可改性、黑名單機制
    - 持有人數量與集中度、是否上架 CEX/DEX

    支援 Ethereum、BSC、Arbitrum、Polygon、Base 等 11+ 鏈。
    完全免費、無需 API 金鑰。

    Args:
        contract_address: 代幣合約地址（0x 開頭，42 字符）
        chain_id: 區塊鏈 ID（預設 1=Ethereum；56=BSC, 42161=Arbitrum, 137=Polygon, 8453=Base）
    """
    if not contract_address.startswith("0x") or len(contract_address) != 42:
        return "❌ Invalid contract address format. The address must start with 0x and be 42 characters long."

    cid = str(chain_id)
    try:
        resp = httpx.get(
            f"{_GOPLUS_BASE}/token_security/{cid}",
            params={"contract_addresses": contract_address},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"❌ GoPlus query failed: {e}"

    if data.get("code") != 1:
        return f"❌ GoPlus response error: {data.get('message', 'unknown error')} (code={data.get('code')})"

    result = data.get("result", {})
    info = result.get(contract_address.lower()) or result.get(contract_address)
    if not info:
        return (
            f"❌ No security data found for this contract on {_chain_label(cid)}. "
            "Please verify the address and chain are correct."
        )

    # 側錄 raw info 供 HITL 詐騙判定證據鏈使用（不改下方 markdown 回傳值）
    _stash_token_security_raw(contract_address, cid, info)
    return _format_token_security(info, cid)


def _extract_risk_signals(info: dict) -> dict:
    """從 GoPlus token security raw 結果萃取結構化風險信號。

    與 _format_token_security 共用同一套欄位判斷口徑（保持一致），
    但回傳結構化 dict 而非 markdown，供 HITL 場景（證據鏈展示 / 信心分
    計算）使用。不改變既有 check_token_security 的 markdown 輸出。

    Returns:
        {
            "high_risk": [str, ...],      # 高風險信號 key（如 "is_honeypot"）
            "warnings": [str, ...],       # 警告信號 key
            "safeties": [str, ...],       # 安全信號 key
            "metrics": {                  # 關鍵數值指標
                "buy_tax": str, "sell_tax": str,
                "owner_percent": str, "creator_percent": str,
                "holder_count": str, "total_supply": str,
                "top3_holder_percent": float | None,
            },
            "is_trusted": bool,           # 在信任清單 / 已上架 CEX
            "verdict": "high_risk" | "warning" | "trusted_with_permissions"
                     | "normal" | "insufficient",
        }
    """
    risks = []
    warnings = []
    safeties = []

    # ── 高風險（對齊 _format_token_security:178-202）──
    if info.get("is_honeypot") == "1":
        risks.append("is_honeypot")
    if info.get("cannot_buy") == "1":
        risks.append("cannot_buy")
    if info.get("is_mintable") == "1" and info.get("owner_change_balance") == "1":
        risks.append("mintable_and_owner_change_balance")
    if info.get("selfdestruct") == "1":
        risks.append("selfdestruct")
    if info.get("honeypot_with_same_creator") == "1":
        risks.append("honeypot_with_same_creator")

    # ── 警告（對齊 _format_token_security:185-218）──
    if info.get("is_open_source") != "1":
        warnings.append("not_open_source")
    if info.get("hidden_owner") == "1":
        warnings.append("hidden_owner")
    if info.get("slippage_modifiable") == "1":
        warnings.append("slippage_modifiable")
    if info.get("is_proxy") == "1":
        warnings.append("is_proxy")
    if info.get("transfer_pausable") == "1":
        warnings.append("transfer_pausable")
    if info.get("is_mintable") == "1" and info.get("owner_change_balance") != "1":
        warnings.append("mintable")
    if info.get("external_call") == "1":
        warnings.append("external_call")

    # ── 安全指標（對齊 _format_token_security:221-229）──
    if info.get("trust_list") == "1":
        safeties.append("trust_list")
    cex_info = info.get("is_in_cex", {})
    if isinstance(cex_info, dict) and cex_info.get("listed") == "1":
        safeties.append("is_in_cex")
    if info.get("is_in_dex") == "1":
        safeties.append("is_in_dex")

    is_trusted = info.get("trust_list") == "1" or bool(safeties)

    # ── verdict（對齊 _format_token_security:258-268）──
    if risks and not is_trusted:
        verdict = "high_risk"
    elif risks and is_trusted:
        verdict = "trusted_with_permissions"
    elif warnings:
        verdict = "warning"
    elif safeties:
        verdict = "normal"
    else:
        verdict = "insufficient"

    # ── 數值指標（對齊 _format_token_security:275-290）──
    holders = info.get("holders", [])
    top3_pct = None
    if holders and len(holders) >= 3:
        try:
            top3_pct = round(
                sum(float(h.get("percent", 0)) for h in holders[:3]) * 100, 2
            )
        except (ValueError, TypeError):
            top3_pct = None

    return {
        "high_risk": risks,
        "warnings": warnings,
        "safeties": safeties,
        "metrics": {
            "buy_tax": info.get("buy_tax", "N/A"),
            "sell_tax": info.get("sell_tax", "N/A"),
            "owner_percent": info.get("owner_percent", "N/A"),
            "creator_percent": info.get("creator_percent", "N/A"),
            "holder_count": info.get("holder_count", "N/A"),
            "total_supply": info.get("total_supply", "N/A"),
            "top3_holder_percent": top3_pct,
        },
        "is_trusted": is_trusted,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HITL 用：側錄 raw info 供詐騙判定證據鏈使用（不改 LLM 看到的 markdown 輸出）
# ─────────────────────────────────────────────────────────────────────────────
# process-local stash，仿 _cached_token（:53）模式。用 list 處理一輪查多合約；
# pop 時清空避免跨請求殘留。單一 asyncio loop 內安全（agent 不跨 process）。
_last_token_security_raw: list[dict] = []


def _stash_token_security_raw(contract_address: str, chain_id, info: dict) -> None:
    """側錄一次 GoPlus token security 查詢的 raw info（含合約地址與鏈）。

    check_token_security 在 return markdown 前呼叫，供 claw_loop 攔截後用
    _extract_risk_signals + risk_scoring 組證據鏈。不改 LLM 看到的回傳值。
    """
    _last_token_security_raw.append(
        {"contract_address": contract_address, "chain_id": chain_id, "info": info}
    )


def pop_token_security_raw() -> list[dict]:
    """取出並清空側錄的 raw info（消費式，避免跨請求殘留）。"""
    stashed = list(_last_token_security_raw)
    _last_token_security_raw.clear()
    return stashed


def _format_token_security(info: dict, chain_id: str) -> str:
    """把 GoPlus token security 回應格式化成 markdown 風險報告。"""
    token_name = info.get("token_name", "Unknown")
    token_symbol = info.get("token_symbol", "")
    symbol_str = f" ({token_symbol})" if token_symbol else ""

    # ── 風險等級判定 ──
    risks = []
    warnings = []

    is_honeypot = info.get("is_honeypot") == "1"
    is_mintable = info.get("is_mintable") == "1"
    owner_change_balance = info.get("owner_change_balance") == "1"
    selfdestruct = info.get("selfdestruct") == "1"
    cannot_buy = info.get("cannot_buy") == "1"
    honeypot_same_creator = info.get("honeypot_with_same_creator") == "1"

    is_open_source = info.get("is_open_source") == "1"
    hidden_owner = info.get("hidden_owner") == "1"
    slippage_modifiable = info.get("slippage_modifiable") == "1"
    is_proxy = info.get("is_proxy") == "1"
    transfer_pausable = info.get("transfer_pausable") == "1"
    external_call = info.get("external_call") == "1"

    # 高風險（紅色）
    if is_honeypot:
        risks.append("🔴 **Honeypot**: buyable but not sellable — a classic scam")
    if cannot_buy:
        risks.append("🔴 **Cannot buy**: the contract blocks purchases")
    if is_mintable and owner_change_balance:
        risks.append("🔴 **Owner can mint infinitely + change your balance**: rug pull precursor")
    if selfdestruct:
        risks.append("🔴 **Contract can self-destruct**: funds could go to zero")
    if honeypot_same_creator:
        risks.append("🔴 **Same creator previously deployed honeypot contracts**: high-risk address")

    # 警告（黃色）
    if not is_open_source:
        warnings.append("🟡 **Contract not open-source**: cannot be audited, risk unknown")
    if hidden_owner:
        warnings.append("🟡 **Hidden owner**: contract appears to renounce ownership but actually retains it")
    if slippage_modifiable:
        warnings.append("🟡 **Slippage/tax modifiable**: buy/sell tax could be raised unexpectedly")
    if is_proxy:
        warnings.append("🟡 **Proxy contract**: logic can be swapped")
    if transfer_pausable:
        warnings.append("🟡 **Transfers can be paused**: owner can freeze your tokens")
    if is_mintable and not owner_change_balance:
        warnings.append("🟡 **Mintable**: could dilute holders' value")
    if external_call:
        warnings.append("🟡 **External calls**: the contract interacts with other contracts on execution")

    # ── 安全指標（綠色）──
    safeties = []
    if info.get("trust_list") == "1":
        safeties.append("✅ On the GoPlus trust list")
    cex_info = info.get("is_in_cex", {})
    if isinstance(cex_info, dict) and cex_info.get("listed") == "1":
        cex_list = cex_info.get("cex_list", [])
        safeties.append(f"✅ Listed on CEX: {', '.join(cex_list)}")
    if info.get("is_in_dex") == "1":
        safeties.append("✅ Has liquidity on DEX")

    # ── 組裝報告 ──
    lines = [f"## 🛡️ Token Security Check: {token_name}{symbol_str}"]
    holder_count = info.get("holder_count", "N/A")
    try:
        holder_count = f"{int(holder_count):,}"
    except (ValueError, TypeError):
        pass
    lines.append(f"**Chain**: {_chain_label(chain_id)} | **Holders**: {holder_count}")
    lines.append("")

    # 風險摘要
    if risks:
        lines.append("### 🔴 High-Risk Alerts")
        lines.extend(f"- {r}" for r in risks)
        lines.append("")
    if warnings:
        lines.append("### 🟡 Warnings")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")
    if safeties:
        lines.append("### 🟢 Safety Indicators")
        lines.extend(f"- {s}" for s in safeties)
        lines.append("")

    # 總體評級
    # trust_list / CEX 上市 代幣即使有 mintable 等權限（如 USDT）也降級為「需注意」，
    # 因為這些是合規中心化代幣的正常權限，不是 rug pull 風險。
    is_trusted = info.get("trust_list") == "1" or bool(safeties)
    if risks and not is_trusted:
        verdict = "🔴 **High Risk** — strongly advised to avoid"
    elif risks and is_trusted:
        verdict = "🟡 **Trusted token but with permission risks** — permissions serve normal operations, but stay cautious"
    elif warnings:
        verdict = "🟡 **Risk signals detected** — evaluate carefully"
    elif safeties:
        verdict = "🟢 **Looks normal** — but always do your own research (DYOR)"
    else:
        verdict = "⚪ **Insufficient data** — cannot determine"

    lines.append(f"**Overall Rating**: {verdict}")
    lines.append("")

    # ── 詳細數據 ──
    lines.append("### 📊 Detailed Data")
    buy_tax = info.get("buy_tax", "N/A")
    sell_tax = info.get("sell_tax", "N/A")
    lines.append(f"- **Buy Tax**: {buy_tax}% | **Sell Tax**: {sell_tax}%")

    owner_pct = info.get("owner_percent", "N/A")
    creator_pct = info.get("creator_percent", "N/A")
    lines.append(f"- **Owner Holdings**: {owner_pct}% | **Creator Holdings**: {creator_pct}%")

    total_supply = info.get("total_supply", "N/A")
    lines.append(f"- **Total Supply**: {total_supply}")

    # top holders 集中度
    holders = info.get("holders", [])
    if holders and len(holders) >= 3:
        top3_pct = sum(float(h.get("percent", 0)) for h in holders[:3]) * 100
        lines.append(f"- **Top 3 Holders Combined**: {top3_pct:.1f}%")

    lines.append("")
    lines.append("*(Source: GoPlus Security API)*")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Address Security（平台官方 key，自動換 token）
# ─────────────────────────────────────────────────────────────────────────────


@tool
def check_address_safety(address: str, chain_id: int = 1) -> str:
    """檢測區塊鏈地址是否涉及惡意行為（釣魚、洗錢、制裁、混幣器等）。

    查詢錢包地址或合約地址的安全信譽，偵測：
    - 釣魚活動 (phishing_activities)
    - 洗錢 (money_laundering)
    - 制裁名單 (sanctioned)
    - 混幣器 (mixer)
    - 勒索 (blackmail_activities)
    - 竊盜攻擊 (stealing_attack)
    - 網路犯罪 (cybercrime)
    - 金融犯罪 (financial_crime)
    - 暗網交易 (darkweb_transactions)
    - 偽造 KYC (fake_kyc)
    - 蜂蜜罐關聯 (honeypot_related_address)

    使用平台官方 GoPlus key，使用者無需自行設定。

    Args:
        address: 要查詢的地址（0x 開頭）
        chain_id: 區塊鏈 ID（預設 1=Ethereum）
    """
    if not address.startswith("0x") or len(address) != 42:
        return "❌ Invalid address format. The address must start with 0x and be 42 characters long."

    raw = fetch_address_security_raw(address, chain_id)

    if raw["error"]:
        return f"❌ {raw['error']}"
    info = raw["info"]
    if not info:
        return (
            f"ℹ️ GoPlus has no records for address `{address}` on {_chain_label(str(chain_id))}. "
            "No records does not mean the address is safe — it may simply be new or not yet reported. Stay vigilant."
        )

    return _format_address_safety(info, address, str(chain_id))


def fetch_address_security_raw(address: str, chain_id: int = 1) -> dict:
    """GoPlus address_security 原始取用（健診 API 用；同步 httpx）。

    Returns:
        {"info": dict | None, "error": str | None}——不丟例外，失敗訊息放 error
        （含「無 GoPlus key」與「查無紀錄」的資訊性情境，info 為空 dict）。
    """
    if not address.startswith("0x") or len(address) != 42:
        return {"info": None, "error": "Invalid EVM address format"}

    token = _get_goplus_token()
    if not token:
        return {
            "info": None,
            "error": "GoPlus key not configured (GOPLUS_APP_KEY / GOPLUS_APP_SECRET)",
        }

    try:
        resp = httpx.get(
            f"{_GOPLUS_BASE}/address_security/{address}",
            params={"chain_id": str(chain_id)},
            headers={"Authorization": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"info": None, "error": f"GoPlus address lookup failed: {e}"}

    if data.get("code") != 1:
        msg = data.get("message", "unknown error")
        return {"info": None, "error": f"GoPlus response error: {msg} (code={data.get('code')})"}

    return {"info": data.get("result", {}) or {}, "error": None}


# GoPlus 惡意行為旗標（值 "1" = 命中）；健診判定與 LLM 格式化共用同一清單
MALICIOUS_FIELDS = {
    "phishing_activities": "phishing",
    "money_laundering": "money_laundering",
    "sanctioned": "sanctioned",
    "mixer": "mixer",
    "blackmail_activities": "blackmail",
    "stealing_attack": "stealing_attack",
    "cybercrime": "cybercrime",
    "financial_crime": "financial_crime",
    "darkweb_transactions": "darkweb_transactions",
    "fake_kyc": "fake_kyc",
    "honeypot_related_address": "honeypot_related",
    "malicious_mining_activities": "malicious_mining",
    "gas_abuse": "gas_abuse",
    "blacklist_doubt": "blacklist_doubt",
}


def goplus_malicious_flags(info: dict) -> list[str]:
    """回傳命中的惡意旗標清單（原始欄位名；健診 reasons 用）。"""
    if not isinstance(info, dict):
        return []
    return [field for field in MALICIOUS_FIELDS if info.get(field) == "1"]


def _format_address_safety(info: dict, address: str, chain_id: str) -> str:
    """把 GoPlus address security 回應格式化成 markdown 報告。"""
    # 偵測到的惡意行為
    malicious_fields = {
        "phishing_activities": "Phishing activity",
        "money_laundering": "Money laundering",
        "sanctioned": "Sanctions list",
        "mixer": "Mixer",
        "blackmail_activities": "Blackmail",
        "stealing_attack": "Stealing attack",
        "cybercrime": "Cybercrime",
        "financial_crime": "Financial crime",
        "darkweb_transactions": "Dark web transactions",
        "fake_kyc": "Fake KYC",
        "honeypot_related_address": "Honeypot-related address",
        "malicious_mining_activities": "Malicious mining",
        "gas_abuse": "Gas abuse",
        "blacklist_doubt": "Suspected malicious (blacklist doubt)",
    }

    flagged = []
    for field, label in malicious_fields.items():
        if info.get(field) == "1":
            flagged.append(label)

    is_contract = info.get("contract_address") == "1"
    addr_type = "smart contract" if is_contract else "wallet address"

    lines = [f"## 🔍 Address Safety Check: {address[:10]}...{address[-6:]}"]
    lines.append(f"**Type**: {addr_type} | **Chain**: {_chain_label(chain_id)}")
    lines.append("")

    if flagged:
        lines.append("### 🔴 Malicious Activity Detected")
        for f in flagged:
            lines.append(f"- 🔴 **{f}**")
        lines.append("")
        lines.append("**⚠️ Strongly advised: do not interact with this address or send funds to it.**")
    else:
        lines.append("### 🟢 No Known Malicious Activity Detected")
        lines.append("This address has no malicious records in the GoPlus database.")
        lines.append("(Note: no records does not guarantee safety — stay vigilant)")

    lines.append("")

    # 額外資訊
    num_malicious_created = info.get("number_of_malicious_contracts_created", "0")
    if num_malicious_created and num_malicious_created != "0":
        lines.append(f"- **Malicious Contracts Created**: {num_malicious_created}")

    data_source = info.get("data_source", "")
    if data_source:
        lines.append(f"- **Data Source**: {data_source}")

    lines.append("")
    lines.append("*(Source: GoPlus Security API)*")
    return "\n".join(lines)
