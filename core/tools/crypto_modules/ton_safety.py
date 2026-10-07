"""
TON jetton 安全評估——兩個角色：

1. **swap workflow 的安全 node**（既有）：`assess_jetton_safety(address) -> TokenSafety`
   供 deterministic swap subgraph 呼叫（目前已停用，swap 整體關閉）。

2. **agent 詐騙檢測工具**（路線 B 新增）：`assess_jetton_safety_tool(address) -> str`
   註冊成 LangChain @tool，讓 cryptomind agent 能查 TON jetton 安全性，
   並接上詐騙證據 HITL 流程（與 GoPlus 的 check_token_security 對稱）。
   docs/plans/2026-08-11-ton-scam-detection-strengthen-design.md

設計理念：
  - GoPlus 不支援 TON；TonAPI 的 verification 欄位是 TON 生态最強的
    反詐騙訊號（whitelist = 官方驗證）。holders_count 與 admin 補強。
  - 評估結果是結構化資料（TokenSafety dataclass）。
  - 驗證狀態分級規則在 safety_kernel.safety_rules（審查核心單一來源）。

資料源：TonAPI.io（v2/jettons endpoint，無需 API key）。
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from langchain_core.tools import tool

from safety_kernel.safety_rules import (  # noqa: F401  — re-export 給既有呼叫端
    LOW_HOLDER_THRESHOLD,
    TokenSafety,
    build_token_safety,
    safety_to_dict,
)

logger = logging.getLogger(__name__)

_TONAPI_BASE = "https://tonapi.io"

# friendly = 48 字元 base64url（前兩字元由 flag＋workchain 決定：EQ/UQ/kQ/0Q/Ef/Uf… 都合法）；
# raw = workchain:64 hex（basechain 0／masterchain -1）。TonAPI 兩種都吃。
_FRIENDLY_TON_ADDRESS = re.compile(r"[A-Za-z0-9_-]{48}")
_RAW_TON_ADDRESS = re.compile(r"(?:0|-1):[0-9a-fA-F]{64}")

# ── Raw-data stash（供 claw_loop 詐騙證據流程取用，仿 goplus._stash_token_security_raw）──
_TON_SAFETY_STASH: list[dict] = []


def _stash_ton_safety_raw(address: str, safety: TokenSafety) -> None:
    """側錄原始 TokenSafety 供 HITL 詐騙證據流程取用。"""
    _TON_SAFETY_STASH.append({"address": address, "safety": safety_to_dict(safety)})


def pop_ton_safety_raw() -> list[dict]:
    """取出並清空 stash（消費端：claw_loop._build_scam_evidence）。"""
    global _TON_SAFETY_STASH
    stashed = _TON_SAFETY_STASH
    _TON_SAFETY_STASH = []
    return stashed


def assess_jetton_safety(address: str) -> TokenSafety:
    """查 TonAPI 評估一個 TON jetton 的安全性。

    同步函式（TonAPI 是同步 httpx）。供 swap workflow 的安全 node 呼叫。
    不丟例外——TonAPI 不可用時回傳 unknown 評估，讓 workflow 決定降級。

    Args:
        address: TON jetton master 地址（48 字元 friendly 或 0:/-1: raw 格式）。

    Returns:
        TokenSafety 評估結果（規則由 safety_kernel 定義）。
    """
    addr = (address or "").strip()
    if not (_FRIENDLY_TON_ADDRESS.fullmatch(addr) or _RAW_TON_ADDRESS.fullmatch(addr)):
        return _unknown_safety(addr, reason="invalid TON address format")

    try:
        resp = httpx.get(
            f"{_TONAPI_BASE}/v2/jettons/{addr}",
            timeout=15.0,
            headers={"Origin": "https://tonapi.io"},
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("TonAPI jetton lookup failed for %s: %s", addr[:20], exc)
        return _unknown_safety(addr, reason="TonAPI unavailable")

    # 代幣不存在 → 強訊號（貼錯地址或詐騙）
    if resp.status_code == 400 or resp.status_code == 404:
        return build_token_safety(
            address=addr,
            verification="none",
            symbol="",
            name="(token does not exist)",
            holders_count=0,
            has_admin=False,
            exists=False,
            extra_signals=[
                "Token does not exist on the TON chain (possibly a scam address or a mistyped address)"
            ],
        )
    if resp.status_code != 200:
        logger.warning("TonAPI returned %s for %s", resp.status_code, addr[:20])
        return _unknown_safety(addr, reason=f"TonAPI HTTP {resp.status_code}")

    body = resp.json()
    meta = body.get("metadata", {})
    return build_token_safety(
        address=addr,
        verification=body.get("verification", "none"),
        symbol=meta.get("symbol", body.get("name", "")),
        name=meta.get("name", body.get("name", "")),
        holders_count=int(body.get("holders_count", 0)),
        has_admin=bool(body.get("admin")),
        exists=True,
    )


def _unknown_safety(address: str, reason: str) -> TokenSafety:
    """無法評估時的降級結果（不丟例外，讓 workflow 決定怎麼處理）。"""
    return build_token_safety(
        address=address,
        verification="unknown",
        symbol="",
        name="",
        holders_count=0,
        has_admin=False,
        exists=False,
        extra_signals=[f"Unable to complete safety assessment: {reason}"],
    )


# ── Agent 詐騙檢測工具（路線 B）─────────────────────────────────────────────
# 仿 goplus.check_token_security：查 TON jetton 安全 + stash raw + 回 markdown。
# 註冊成 @tool 讓 cryptomind agent 呼叫；claw_loop 偵測 used_tools 含本工具
# → pop_ton_safety_raw → _extract_ton_signals → score_scam_confidence → 詐騙卡。

@tool
def assess_jetton_safety_tool(address: str) -> str:
    """查詢 TON jetton 的鏈上安全訊號（白名單驗證 / 持有人數 / admin 權限）。

    輸入 TON jetton master 地址（EQ/UQ/Ef… 等 48 字元 friendly，或 0:… raw 格式）。回傳結構化的安全評估報告。
    完全免費、無需金鑰（TonAPI 公開端點）。

    這是 TON 專屬的安全檢測工具（GoPlus check_token_security 不支援 TON）。
    遇到 TON 地址（EQ/UQ/Ef… 開頭的 48 字元，或 0:… raw）時用此工具；遇到 EVM 地址（0x 開頭）時用 check_token_security。
    """
    safety = assess_jetton_safety(address)
    _stash_ton_safety_raw(address, safety)  # 側錄供 HITL 詐騙證據流程
    return _format_ton_safety_report(safety)


def _format_ton_safety_report(safety: TokenSafety) -> str:
    """把 TokenSafety 格式化成 markdown 報告（給 LLM 看）。"""
    d = safety_to_dict(safety)
    name = d.get("name", "") or "(unknown)"
    symbol = d.get("symbol", "") or ""
    verification = d.get("verification", "unknown")
    holders = d.get("holders_count", 0)
    has_admin = d.get("has_admin", False)
    exists = d.get("exists", True)
    signals = d.get("signals", [])

    title = "## 🛡️ TON Jetton Safety Report"
    lines = [
        title,
        "",
        f"**Address**: `{d.get('address', '')}`",
        f"**Token**: {name} ({symbol})" if symbol else f"**Token**: {name}",
        f"**Verification**: {verification}",
        f"**Holders**: {holders:,}",
        f"**Has Admin**: {'⚠️ Yes' if has_admin else 'No'}",
    ]

    if not exists:
        lines.append("\n🚨 **Token does not exist on TON chain** — possibly a scam or mistyped address.")
    elif signals:
        lines.append("\n**Signals:**")
        for s in signals:
            lines.append(f"- {s}")

    if verification == "whitelist" and exists:
        lines.append("\n✅ This jetton is on the TON official whitelist (verified).")
    elif verification != "whitelist" and exists:
        lines.append("\n⚠️ This jetton is NOT officially verified (not whitelisted). Proceed with caution.")

    return "\n".join(lines)


def _extract_ton_signals(safety_dict: dict) -> dict:
    """把 TokenSafety dict 轉成詐騙評分器吃的 signals dict。

    輸出 shape 與 goplus._extract_risk_signals 對齊：
        {high_risk, warnings, safeties, metrics, is_trusted, verdict}
    verdict 邏輯與 GoPlus 一致（high_risk > trusted_with_permissions > warning > normal > insufficient）。
    """
    risks: list[str] = []
    warnings: list[str] = []
    safeties: list[str] = []

    # 代幣不存在 = 高風險（詐騙或打錯）
    if not safety_dict.get("exists", True):
        risks.append("token_not_found")

    # 未白名單
    if safety_dict.get("exists", True) and not safety_dict.get("is_verified"):
        warnings.append("not_whitelisted")

    # 低持有人
    if safety_dict.get("is_low_liquidity"):
        warnings.append("is_low_liquidity")

    # 有 admin 權限
    if safety_dict.get("has_admin"):
        warnings.append("has_admin")

    # 白名單 = 安全訊號
    if safety_dict.get("is_verified"):
        safeties.append("ton_whitelisted")

    # verdict（與 GoPlus _extract_risk_signals 同邏輯）
    is_trusted = bool(safeties)
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

    return {
        "high_risk": risks,
        "warnings": warnings,
        "safeties": safeties,
        "metrics": {
            "holder_count": str(safety_dict.get("holders_count", 0)),
        },
        "is_trusted": is_trusted,
        "verdict": verdict,
    }
