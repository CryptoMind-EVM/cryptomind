"""同意閘規則 + 同意足跡 hash（純規則，safety-kernel）。

「涉及金額不論大小都應該確認」的規則化（審查核心）：
  - CONSENT_REQUIRED_LEVELS：risk_tiers 中觸發同意卡的等級。
    （orange=3-5%、red=5-15%；block 直接擋不需要同意。）
  - consent_payload_hash：把「使用者同意的是什麼」做成 canonical SHA-256。
    每次 swap confirm 把 hash 寫進 swap_executions.consent_hash——事後可
    對帳：同意足跡不可否認、不可被靜默修改。

純函式、無 I/O——審查員可逐行核對哪些等級需要同意、hash 怎麼算。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# 需要顯式同意的風險等級。block 不在此列——blocked 直接硬擋，沒有同意選項。
CONSENT_REQUIRED_LEVELS = frozenset({"orange", "red"})

# 納入同意 hash 的欄位（swap_order_token payload 的 key）。
# 只挑「使用者同意的事實」：換什麼、換多少、最低收到量、風險等級、價格影響、
# 使用的 kernel 版本。不含時效/內部欄位（e / w / f / ou / fu）。
_CONSENT_FACT_KEYS = ("q", "u", "i", "a", "o", "m", "pi", "rl", "kv")

# 確定不需要同意的等級（block 是硬擋，不是同意）。
_NON_CONSENT_LEVELS = frozenset({"green", "yellow", "block"})


def requires_consent(risk_level: str) -> bool:
    """這個風險等級是否需要顯式同意才能執行。

    未知等級 fail-closed：不在已知集合 → 保守要求同意。
    """
    if risk_level in CONSENT_REQUIRED_LEVELS:
        return True
    return risk_level not in _NON_CONSENT_LEVELS


def consent_payload_hash(payload: Mapping[str, Any]) -> str:
    """把「使用者同意的事實」做成 canonical SHA-256 hex。

    只取 _CONSENT_FACT_KEYS（缺失的欄位視為空字串），JSON 序列化用
    sort_keys + 緊湊分隔（與 swap_order_token 的簽章序列化一致），
    保證「相同的同意事實 → 相同的 hash」。

    Args:
        payload: swap_order_token 的 decoded payload（dict）。

    Returns:
        SHA-256 hex digest（64 chars）。
    """
    facts = {key: str(payload.get(key, "")) for key in _CONSENT_FACT_KEYS}
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
