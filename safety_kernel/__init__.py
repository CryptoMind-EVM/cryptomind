"""cryptomind-safety-kernel — verifiable risk-gating rules for agent-driven money operations.

「審查核心」的實體化（docs/plans/2026-08-04-omniston-defi-skill-design.md 的
「可驗證性」擴充）：
  - 純規則、零依賴、零網路 I/O——不查 CoinGecko、不查 TonAPI、不碰 DB。
  - 單一來源：主平台（CryptoMind）的 swap 風險邏輯一律 import 這裡，
    不允許在別處重複定義閾值。
  - 公開可驗證：此套件同步鏡像到公開 repo（SOURCE_URL），主平台
    GET /api/swap/kernel-version 回傳的 version 必須等於公開 repo 的 tag，
    審查員/審計員可比對「線上跑的規則」與「開源的規則」是否同一份。

涵蓋的規則（審查核心六件套中的純規則部分）：
  - risk_tiers   — price impact 色階（1/3/5/15%）+ 綜合風險分級
  - safety_rules — jetton 安全訊號（驗證/持有者/admin）
  - swap_limits  — 單筆 USD 限額（fail-closed）
  - consent_gate — 哪些等級需要同意 + 同意足跡的 canonical hash

不含（刻意）：網路呼叫、簽章、DB、前端。那些是主平台的責任。
"""

__version__ = "0.1.0"

# 公開鏡像 repo（審查員驗證「線上 = 開源」的對照來源）。
# Internal package: the former public repository has been retired.
SOURCE_URL = ""

from safety_kernel.consent_gate import (  # noqa: E402
    CONSENT_REQUIRED_LEVELS,
    consent_payload_hash,
    requires_consent,
)
from safety_kernel.risk_tiers import (  # noqa: E402
    TIER_BLOCK_PCT,
    TIER_GREEN_PCT,
    TIER_RED_PCT,
    TIER_YELLOW_PCT,
    RiskAssessment,
    assess_risk,
)
from safety_kernel.safety_rules import (  # noqa: E402
    LOW_HOLDER_THRESHOLD,
    TokenSafety,
    build_token_safety,
    safety_to_dict,
)
from safety_kernel.swap_limits import (  # noqa: E402
    SwapLimitCheck,
    check_limit_with_price,
)

__all__ = [
    "__version__",
    "SOURCE_URL",
    "TIER_GREEN_PCT",
    "TIER_YELLOW_PCT",
    "TIER_RED_PCT",
    "TIER_BLOCK_PCT",
    "RiskAssessment",
    "assess_risk",
    "LOW_HOLDER_THRESHOLD",
    "TokenSafety",
    "build_token_safety",
    "safety_to_dict",
    "SwapLimitCheck",
    "check_limit_with_price",
    "CONSENT_REQUIRED_LEVELS",
    "requires_consent",
    "consent_payload_hash",
]
