"""Feature flags（design.md §21 回滾策略／impl plan 部署順序）。

所有新能力預設關閉；env 未設 = off。判定準則與 MCP_ENABLED 一致
（"1"/"true"/"yes"，大小寫不拘）。
"""

from __future__ import annotations

import logging
import os

_TRUTHY = ("1", "true", "yes", "on")


def env_flag(env_key: str, default: str = "") -> bool:
    """全專案唯一的旗標判定。

    2026-09-04 之前每個模組各自寫一套：``core/config.py`` 用
    ``.lower() == "true"``（**"1" 會被判成 false**）、``api/vision.py`` 收
    ``1/true/yes/on``、``feature_flags`` 收 ``1/true/yes``。同一個 ``=1``
    在不同旗標上意思相反，而且不會噴錯——只會安靜地沒生效。統一收斂到這裡。

    2026-09-27：後台覆寫（core/setting_overrides.py）優先於 env——只有白名單裡的旗標會查。
    """
    raw = _override_raw(env_key)
    if raw is None:
        raw = os.getenv(env_key, default)
    return raw.strip().lower() in _TRUTHY


def _override_raw(env_key: str):
    from core import setting_overrides

    if env_key not in setting_overrides.OVERRIDABLE_FLAGS:
        return None
    try:
        return setting_overrides.get(env_key)
    except Exception:  # noqa: BLE001 — 覆寫讀不到就用 env
        return None


# 舊名保留（本檔內部大量使用）
_flag = env_flag


def agent_presets_enabled() -> bool:
    """Phase 2：preset API；off 時 preset endpoints 404、analyze 忽略 preset_id。"""
    return _flag("AGENT_PRESETS_ENABLED")


def manifund_mcp_enabled() -> bool:
    """Phase 3：Manifund MCP server（另需 MCP_ENABLED=1）。"""
    return _flag("MANIFUND_MCP_ENABLED")


def loop_fork_enabled() -> bool:
    """Model Mixer Step 6：loop_fork 軟門檻分岔（HITL 第 9 種）。

    off = 工具步數不設軟門檻（現況：跑到 LangGraph 的 recursion_limit 為止）；
    on = 步數觸及 _FORK_SOFT_STEPS 時以 interrupt 卡片問人要收尾還是補提示。

    這步原本沒有開關——它掛在單節點路徑上，deploy 完就對所有使用者生效，
    而且是新的使用者可見行為。其餘 Mixer 步驟（Router）都有一鍵
    回滾，這裡補齊，讓三步的灰度與回滾路徑一致。
    docs/plans/2026-09-03-model-mixer-step6-hitl.md
    """
    return _flag("MIXER_LOOP_FORK_ENABLED")


def router_enabled() -> bool:
    """Model Mixer Step 3：LLM Router 分流（取代 T0/T1 的分流職責）。

    off = #617 的 T0→T1 路徑原樣保留（回滾路徑）；on = 單一 Router
    決策（T0 詞表降級為確定性快取、金融訊號硬否決不變）。
    docs/plans/2026-09-03-model-mixer-step3-router.md
    """
    return _flag("ROUTER_ENABLED")


def people_projects_discover_enabled() -> bool:
    """Phase 3：探索介面（Discover）。"""
    return _flag("PEOPLE_PROJECTS_DISCOVER_ENABLED")


def discover_ai_review_enabled() -> bool:
    """出資者旅程 P0-3：AI 出資者視角評估卡（design 2026-08-15）。

    子 flag 灰度用：需主 flag（Discover）＋ MCP 都開才可能 true，
    預設 off —— 上線後由 Zeabur env 控制，回滾一鍵關。
    """
    return (
        _flag("DISCOVER_AI_REVIEW_ENABLED")
        and manifund_mcp_enabled()
        and people_projects_discover_enabled()
    )


def proposal_studio_enabled() -> bool:
    """提案工作台（募資者模式，design 2026-08-16）：預設 off，Zeabur 灰度。"""
    return _flag("PROPOSAL_STUDIO_ENABLED")


def discover_oc_enabled() -> bool:
    """Discover 第二來源：Open Collective 聯邦搜尋（design 2026-08-17 §Phase 1）。

    子 flag 模式（對齊 discover_ai_review_enabled）：需 Discover 主 flag 開才可能
    true，預設 off——Zeabur env 開 DISCOVER_OC_ENABLED 灰度，回滾一鍵關。
    """
    return _flag("DISCOVER_OC_ENABLED") and people_projects_discover_enabled()


def email_brief_enabled() -> bool:
    """PR-8 Email 早報（docs/plans/2026-09-27-pr8-email-brief-impl.md）。

    off = 設定／確認端點 404、cron 不查訂閱表、一封信都不寄（退訂端點例外，永遠可用）。
    on 之外還要寄信服務的 env 全部到位（core/email_brief/provider.missing_config），否則 fail closed。
    2026-09-27 DANNY：預設開——寄信服務設好就自動生效；要關就設 false。
    """
    return _flag("EMAIL_BRIEF_ENABLED", "true")


def moderation_enabled() -> bool:
    """論壇發文／編輯前的內容檢查（2026-10-01 試驗；core/moderation）。

    2026-10-02 起只用自己微調的模型（v7 起 CryptoMind-Guard-0.6B：≥0.8 擋、0.5～0.8 送審），外加「貼出助記詞／私鑰」
    的保護；檢查服務掛了照常發文。關掉＝全部直接通過。
    """
    return _flag("MODERATION_ENABLED", "true")


def crypto_tab_enabled() -> bool:
    """市場板塊的「加密貨幣」子分頁（導覽整理成 AI 助理／市場／社群，2026-10-05）。

    只經 /api/config 的 capabilities.crypto_tab 給前端（再與平台能力表取交集：Play 版一律沒有）。
    預設開；要給特定部署（地區、入口）關掉就設 false，其餘市場不受影響。
    關掉只收起分頁入口與深連結，AI 對話仍可回答加密貨幣問題。
    """
    return _flag("CRYPTO_TAB_ENABLED", "true")


def answer_share_enabled() -> bool:
    """AI 回答快照分享（任務 D，c072）：使用者選一輪問答產生免登入唯讀連結（/s/<token>）。

    **預設關**：它會把使用者的對話內容公開成連結，merge develop 等於上線（VM 自動部署），
    所以先不開，由 DANNY 在後台設定中心（或 env）決定。關閉時所有端點與公開頁都 404、
    前端不顯示分享按鈕；已建立的連結在重新開啟之前也一律打不開。
    """
    return _flag("CONVERSATION_SHARE_ENABLED", "false")


def reown_social_login_enabled() -> bool:
    """PR-6：登入視窗的 Email／Google（Reown 內嵌錢包）＋Premium 刷卡買 USDC。

    只經 /api/config 給前端；前端另外擋 Telegram／Base App／Play（web/js/social-login.js）。
    後端登入邏輯不變：內嵌錢包一樣走 SIWE personal_sign → evm_<addr>。
    docs/plans/2026-09-27-pr6-reown-social-impl.md
    2026-09-27 DANNY：預設開（Reown 遠端 config 查過：social_login 開、沒指定供應商清單）；要關就設 false。
    """
    return _flag("REOWN_SOCIAL_LOGIN_ENABLED", "true")


def daily_brief_enabled() -> bool:
    """每日早報 cron 整批開關（scripts/cron_daily_brief.cron_enabled 用這支）。

    deny-list：只有 false/0/no/off 才關（打錯字維持開，不會安靜停掉早報）；後台覆寫優先。
    """
    raw = _override_raw("DAILY_BRIEF_ENABLED")
    if raw is None:
        raw = os.getenv("DAILY_BRIEF_ENABLED", "true")
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ============================================================================
# 旗標登記處（2026-09-04）
# ============================================================================
# 為什麼需要：旗標散在 config.py / vision.py / mcp_loader.py / tool_compactor.py
# …各處，看 feature_flags.py 會以為專案只有 10 個開關，實際有 20 幾個，而且
# 預設值有的 on 有的 off。登記處**不重寫判定邏輯**——每一項指向既有的
# getter，值永遠是真的執行期值，只是集中在一個地方看得到。
#
# 新增 *_ENABLED 環境變數卻忘了登記，tests/test_feature_flag_registry.py
# 會紅。


def _cfg(attr: str):
    """core.config 的模組級常數（import 時就定型，延遲取值避免循環 import）。"""

    def getter() -> bool:
        from core import config

        return bool(getattr(config, attr))

    # 保留目標名稱：登記處的守衛靠它找得到真正的消費端（包成閉包後
    # __name__ 會變成 "getter"，測試就以為沒人讀這個旗標）。
    getter.__name__ = attr
    return getter


def _call(module_path: str, func_name: str):
    def getter() -> bool:
        import importlib

        return bool(getattr(importlib.import_module(module_path), func_name)())

    getter.__name__ = func_name  # 同上：守衛要靠這個名字找消費端
    return getter


# name -> (getter, 預設, 一句話用途)
FLAG_REGISTRY: dict = {
    # ── Model Mixer ──────────────────────────────────────────────────────
    "AGENT_PRESETS_ENABLED": (agent_presets_enabled, "off", "preset API；Mixer Step 1/2/4 的總開關"),
    "ROUTER_ENABLED": (router_enabled, "off", "Step 3：LLM Router 取代 T0/T1 分流"),
    "MIXER_LOOP_FORK_ENABLED": (loop_fork_enabled, "off", "Step 6：loop_fork 軟門檻分岔卡"),
    # ── 贊助／探索平台 ───────────────────────────────────────────────────
    "MANIFUND_MCP_ENABLED": (manifund_mcp_enabled, "off", "Manifund MCP server（另需 MCP_ENABLED）"),
    "PEOPLE_PROJECTS_DISCOVER_ENABLED": (people_projects_discover_enabled, "off", "Discover 探索介面"),
    "DISCOVER_AI_REVIEW_ENABLED": (discover_ai_review_enabled, "off", "AI 出資者視角評估卡（需主 flag＋MCP）"),
    "DISCOVER_OC_ENABLED": (discover_oc_enabled, "off", "Discover 的 OC 子功能（需主 flag）"),
    "PROPOSAL_STUDIO_ENABLED": (proposal_studio_enabled, "off", "提案工作台（募資者模式）"),
    # ── Agent 行為 ───────────────────────────────────────────────────────
    "MCP_ENABLED": (_call("core.tools.mcp_loader", "_is_enabled"), "off", "MCP 工具總開關"),
    "AGENT_SELF_MANAGE_ENABLED": (_cfg("AGENT_SELF_MANAGE_ENABLED"), "**on**", "agent 自主提議建立/改/刪 skill 與 memory（過 HITL；2026-10-06 起預設開）"),
    "DEEP_AGENTS_PLANNING_ENABLED": (_call("core.agents.base_react_agent", "_planning_enabled"), "off", "複雜多步驟任務的 TodoList 規劃中介層"),
    "PHASED_MODEL_ENABLED": (_call("core.agents.phased_model", "phased_model_enabled"), "off", "分階段模型綁定：規劃回合不推理、最終回答才用推理預算"),
    "CONSENT_TOOL_GUARD_ENABLED": (lambda: env_flag("CONSENT_TOOL_GUARD_ENABLED"), "off", "high-risk 工具的 defense-in-depth consent 攔截"),
    "BYOK_FALLBACK_ENABLED": (lambda: env_flag("BYOK_FALLBACK_ENABLED", "false"), "off", "使用者金鑰失效時改用 server key（涉及 server 端成本，只有骨架）"),
    # ── 安全／合規 ───────────────────────────────────────────────────────
    "AUTH_LOCKOUT_ENABLED": (_call("core.auth_failure_tracker", "_lockout_enabled"), "off", "登入失敗超閾值鎖定"),
    "PII_SCRUB_ENABLED": (_call("core.validators.pii_scrubber", "_scrub_enabled"), "**on**", "PII 遮蔽。刻意不走 env_flag——它是 deny-list（只有 false/0/no/off 才關），打錯字會維持啟用而不是靜默關掉安全功能"),
    # ── 產品功能 ─────────────────────────────────────────────────────────
    "VISION_ENABLED": (_call("api.vision", "vision_enabled"), "**on**", "圖片分析"),
    "GUEST_AGENT_ENABLED": (_call("api.routers.guest", "guest_agent_enabled"), "**on**", "訪客聊天走真正的 agent（唯讀工具、多輪）；關掉＝一律單次回答"),
    "VISION_STORAGE_ENABLED": (_call("api.vision", "image_storage_enabled"), "**on**", "圖片寫入儲存（讀取不受影響）"),
    "MULTICHAIN_ENABLED": (_cfg("MULTICHAIN_ENABLED"), "**on**", "多鏈支援"),
    "TRUST_SCORE_ENABLED": (_cfg("TRUST_SCORE_ENABLED"), "prod off", "信任分數（production 預設關，要顯式開）"),
    "TRUST_EVENT_RECOMPUTE_ENABLED": (_cfg("TRUST_EVENT_RECOMPUTE_ENABLED"), "**on**", "詐騙 DB 命中時立即重算信任分數"),
    "TRUST_EVM_BINDING_ENABLED": (_cfg("TRUST_EVM_BINDING_ENABLED"), "prod off", "EVM 地址綁定（Human Passport 訊號）"),
    "WALLET_MONITOR_ENABLED": (_cfg("WALLET_MONITOR_ENABLED"), "prod off", "錢包監測總開關"),
    "WALLET_MONITOR_PREMIUM_GATE_ENABLED": (_cfg("WALLET_MONITOR_PREMIUM_GATE_ENABLED"), "off", "只服務 active premium。開之前要先通知既有 Free 用戶"),
    "WALLET_MONITOR_EVM_ENABLED": (_cfg("WALLET_MONITOR_EVM_ENABLED"), "**on**", "EVM 鏈監測。⚠️ 隱藏前提：沒有 ETHERSCAN_SERVICE_API_KEY 一律 false，env 設 true 也沒用"),
    # scripts/cron_daily_brief.cron_enabled 也走 daily_brief_enabled（含後台覆寫）
    "DAILY_BRIEF_ENABLED": (daily_brief_enabled, "**on**", "每日早報 cron 整批開關（deny-list：只有 false/0/no/off 才關；--user 單人試送不受影響）"),
    "EMAIL_BRIEF_ENABLED": (email_brief_enabled, "**on**", "Email 早報（雙重確認＋一鍵退訂）。另需 RESEND_API_KEY／EMAIL_FROM／EMAIL_SENDER_POSTAL_ADDRESS，缺一就不寄"),
    "BASE_APP_NOTIFICATIONS_ENABLED": (_call("core.miniapp_notifications.basedev", "flag_on"), "off", "Base App 推播（Dashboard API 以錢包地址推）。另需 BASE_DASHBOARD_API_KEY；首次全體推播要 DANNY 拍板"),
    "MODERATION_ENABLED": (moderation_enabled, "**on**", "論壇發文／留言的內容檢查：自己微調的模型（moderation 容器，分數 ≥ MODERATION_BLOCK_SCORE 擋）；服務掛了照常發文"),
    "CONVERSATION_SHARE_ENABLED": (answer_share_enabled, "off", "AI 回答快照分享：使用者選一輪問答產生免登入唯讀連結（/s/<token>），公開使用者對話內容。預設關；開之前確認過隱私與免責聲明"),
    "CRYPTO_TAB_ENABLED": (crypto_tab_enabled, "**on**", "市場板塊的加密貨幣子分頁（導覽、子分頁列、#crypto 深連結、訪客選單）。關掉＝這個部署沒有加密貨幣分頁，其餘市場與 AI 對話照常；Play 版不論此值一律關"),
    "REOWN_SOCIAL_LOGIN_ENABLED": (reown_social_login_enabled, "**on**", "登入視窗 Email／Google（Reown 內嵌錢包，固定 EOA）＋Premium 刷卡買 USDC。只在一般網頁生效；開之前 Reown 後台要確認允許網域"),
}

# 已知未接線（技術債）：登記在此、但產品程式碼沒有任何地方讀它。
# 2026-09-04 兩個都接上了，清單清空——留著空集合是刻意的：守衛的例外機制
# 還在，下一個真的暫時修不了的旗標可以登記進來並寫明原因，而不是被迫
# 從 FLAG_REGISTRY 拿掉（那會讓它從稽核視野消失）。
UNWIRED_FLAGS: set = set()

# 跨服務必須一致的旗標：API 與 analysis-worker 是同一題的兩條執行路徑
#（同步串流 vs 背景任務）。只在一邊開，會變成「同一題行為不同」的鬼故事，
# 而且兩邊各自看都正常。
#
# MCP_ENABLED 刻意不在這裡（2026-09-05，線上實測）：worker image 沒有
# mcp-servers（那是主 Dockerfile build 時 clone 的），開機時依檔案存在
# 與否強制關閉（scripts/analysis_worker.py）。env 兩邊都是 true 也要分岔
# ——這是 image 結構性的刻意差異，env 對齊救不了。放進「必須一致」清單
# 只會每次部署都響一次永遠修不掉的誤報，把真正要緊的分岔（Mixer 旗標）
# 淹掉。
CROSS_SERVICE_FLAGS = (
    "AGENT_PRESETS_ENABLED",
    "ROUTER_ENABLED",
    "MIXER_LOOP_FORK_ENABLED",
    "AGENT_SELF_MANAGE_ENABLED",
    "DEEP_AGENTS_PLANNING_ENABLED",
)

# 後台「設定中心」的分組（2026-09-27）：順序就是顯示順序；每個登記旗標恰好出現一次
# （tests/test_admin_settings_center.py 盯著）。
FLAG_GROUPS: dict = {
    "產品功能": (
        "CONVERSATION_SHARE_ENABLED",
        "CRYPTO_TAB_ENABLED",
        "EMAIL_BRIEF_ENABLED",
        "DAILY_BRIEF_ENABLED",
        "REOWN_SOCIAL_LOGIN_ENABLED",
        "MODERATION_ENABLED",
        "VISION_ENABLED",
        "VISION_STORAGE_ENABLED",
        "GUEST_AGENT_ENABLED",
        "BASE_APP_NOTIFICATIONS_ENABLED",
    ),
    "錢包與信任": (
        "WALLET_MONITOR_ENABLED",
        "WALLET_MONITOR_EVM_ENABLED",
        "WALLET_MONITOR_PREMIUM_GATE_ENABLED",
        "MULTICHAIN_ENABLED",
        "TRUST_SCORE_ENABLED",
        "TRUST_EVENT_RECOMPUTE_ENABLED",
        "TRUST_EVM_BINDING_ENABLED",
    ),
    "探索／贊助": (
        "PEOPLE_PROJECTS_DISCOVER_ENABLED",
        "DISCOVER_OC_ENABLED",
        "DISCOVER_AI_REVIEW_ENABLED",
        "PROPOSAL_STUDIO_ENABLED",
        "MANIFUND_MCP_ENABLED",
    ),
    "Agent 行為": (
        "MCP_ENABLED",
        "AGENT_SELF_MANAGE_ENABLED",
        "DEEP_AGENTS_PLANNING_ENABLED",
        "PHASED_MODEL_ENABLED",
        "BYOK_FALLBACK_ENABLED",
    ),
    "Model Mixer": (
        "AGENT_PRESETS_ENABLED",
        "ROUTER_ENABLED",
        "MIXER_LOOP_FORK_ENABLED",
    ),
    "安全／合規": (
        "PII_SCRUB_ENABLED",
        "AUTH_LOCKOUT_ENABLED",
        "CONSENT_TOOL_GUARD_ENABLED",
    ),
}

# 旗標開了也不一定生效：還要其他旗標或環境變數到位。後台設定中心據此顯示「為什麼實際是關的」。
# ("flag", 名稱) = 另一個登記旗標要開；("env", 名稱) = 環境變數要有值；
# ("email_config", None) = Email 寄信設定齊全（core/email_brief/provider.missing_config）。
# 只登記程式裡真的有檢查的依賴——寫錯會讓後台顯示錯誤的原因。
FLAG_REQUIREMENTS: dict = {
    "EMAIL_BRIEF_ENABLED": (("email_config", None),),
    "BASE_APP_NOTIFICATIONS_ENABLED": (("env", "BASE_DASHBOARD_API_KEY"),),
    "VISION_STORAGE_ENABLED": (("flag", "VISION_ENABLED"),),
    "WALLET_MONITOR_EVM_ENABLED": (
        ("flag", "WALLET_MONITOR_ENABLED"),
        ("env", "ETHERSCAN_SERVICE_API_KEY"),
    ),
    "WALLET_MONITOR_PREMIUM_GATE_ENABLED": (("flag", "WALLET_MONITOR_ENABLED"),),
    "DISCOVER_OC_ENABLED": (("flag", "PEOPLE_PROJECTS_DISCOVER_ENABLED"),),
    "DISCOVER_AI_REVIEW_ENABLED": (
        ("flag", "PEOPLE_PROJECTS_DISCOVER_ENABLED"),
        ("flag", "MANIFUND_MCP_ENABLED"),
    ),
    "MANIFUND_MCP_ENABLED": (("flag", "MCP_ENABLED"),),
}

_FLAG_CACHE_KEY = "flags:service:"
_FLAG_CACHE_TTL = 86400
# 每個服務的**全部**旗標快照（設定中心比對各服務用）；上面那把只存跨服務必須一致的 5 個
_FLAG_ALL_CACHE_KEY = "flags:all:"
SNAPSHOT_SERVICES = ("api", "analysis-worker", "cron-worker")


def all_flags() -> dict:
    """所有登記旗標的**執行期實際值**（不是重新解析 env）。"""
    values = {}
    for name, (getter, _default, _note) in FLAG_REGISTRY.items():
        try:
            values[name] = bool(getter())
        except Exception:  # noqa: BLE001 — 讀不到不該擋啟動
            values[name] = None
    return values


def store_service_snapshot(service: str) -> None:
    """把本服務全部旗標的執行期值寫進共用快取（後台設定中心比對各服務用）。失敗就算了。"""
    try:
        from datetime import datetime, timezone

        from core.shared_cache import set_json

        set_json(
            _FLAG_ALL_CACHE_KEY + service,
            {"flags": all_flags(), "at": datetime.now(timezone.utc).isoformat()},
            ttl=_FLAG_CACHE_TTL,
        )
    except Exception:  # noqa: BLE001 — 沒有 Redis 就不比對
        pass


def read_service_snapshots() -> dict:
    """{service: {"flags": {...}, "at": iso}}；沒回報過（或快取過期）的服務不列。"""
    out = {}
    try:
        from core.shared_cache import get_json

        for service in SNAPSHOT_SERVICES:
            snap = get_json(_FLAG_ALL_CACHE_KEY + service)
            if isinstance(snap, dict) and isinstance(snap.get("flags"), dict):
                out[service] = snap
    except Exception:  # noqa: BLE001
        return out
    return out


def flag_snapshot_line(service: str) -> str:
    """啟動時印一行可 grep 的旗標快照（比翻 Zeabur 面板快）。

    2026-09-05：原本把所有開著的旗標混在一個 ``on=`` 裡。實際比對兩個服務時
    （API vs analysis-worker）會看到 5 個差異，但其中只有 1 個要緊——其餘是
    trust／wallet 這類 worker 本來就不跑的路徑。**用眼睛分不出哪個才是問題**，
    而分不出來的警訊等於沒有警訊。

    拆成兩段：``shared=`` 是 CROSS_SERVICE_FLAGS 的子集，兩個服務**必須一樣**，
    也是 FlagDivergence 會盯的那些；``local=`` 是各服務自己的事，不同很正常。
    """
    flags = all_flags()
    on = {k for k, v in flags.items() if v}
    shared = sorted(on & set(CROSS_SERVICE_FLAGS))
    local = sorted(on - set(CROSS_SERVICE_FLAGS))
    return (
        f"[FlagSnapshot] service={service} "
        f"shared={','.join(shared) or '-'} "
        f"local={','.join(local) or '-'} total={len(flags)}"
    )


def check_cross_service_flags(service: str) -> list:
    """把本服務的跨服務旗標寫進共用快取，並比對其他服務。

    回傳不一致清單（每筆 ``(other_service, flag, mine, theirs)``）。呼叫端
    負責記 log——這裡不丟例外，旗標比對絕不可以擋住服務啟動。
    """
    mine = {name: bool(all_flags().get(name)) for name in CROSS_SERVICE_FLAGS}
    divergences = []
    try:
        from core.shared_cache import get_json, set_json

        set_json(_FLAG_CACHE_KEY + service, mine, ttl=_FLAG_CACHE_TTL)
        for other in ("api", "analysis-worker"):
            if other == service:
                continue
            theirs = get_json(_FLAG_CACHE_KEY + other)
            if not isinstance(theirs, dict):
                continue
            for name in CROSS_SERVICE_FLAGS:
                if name in theirs and bool(theirs[name]) != mine[name]:
                    divergences.append((other, name, mine[name], bool(theirs[name])))
    except Exception:  # noqa: BLE001 — 沒有 Redis 就跳過比對
        return divergences
    return divergences


def unknown_flag_lines() -> list[str]:
    """env 裡設了、但 FLAG_REGISTRY 沒登記的 ``*_ENABLED``。

    2026-09-06 線上實測抓到兩類，兩類都不會噴錯：

    1. **已移除的旗標還留在面板上**（``AI_STUDIO_ENABLED``——2026-09-04 因為
       全 repo 只有 getter 與登記兩處、沒有任何消費端而移除）。設著它讓人
       以為某個功能開著。
    2. **打錯字**。``ROUTER_ENABLE``、``ROUTE_ENABLED`` 這種，env_flag 讀不到
       就靜靜地用預設值。

    只掃 ``*_ENABLED`` / ``*_ENABLE`` 後綴：前者是這個專案的旗標命名慣例，
    後者是最常見的漏字。其他 env（金鑰、URL、閾值）不在管轄範圍，掃了只會
    製造雜訊。
    """
    known = set(FLAG_REGISTRY)
    out = []
    for key in sorted(os.environ):
        # 也收 _ENABLE：漏一個 D 是最常見的打錯，而 env_flag 讀不到就靜靜用預設
        if key.endswith(("_ENABLED", "_ENABLE")) and key not in known:
            out.append(
                f"⚠️ [UnknownFlag] {key} 有設值但 FLAG_REGISTRY 沒登記"
                f"——可能是已移除的旗標或打錯字，設了不會有任何作用"
            )
    return out


def log_level_line() -> str:
    """``APP_LOG_LEVEL`` 是否為合法等級。

    2026-09-06 線上實測：Zeabur 面板上兩個變數被貼成同一行，變成
    ``APP_LOG_LEVEL=INFOAUTH_LOCKOUT_ENABLED=true``。``getattr(logging, ...)``
    找不到就靜靜退回 WARNING——**你以為開了 INFO，實際上 INFO 全部看不到**，
    而且那正是你要拿來排查問題的那些 log。同一行還吃掉了另一個旗標。
    """
    raw = os.getenv("APP_LOG_LEVEL")
    if raw is None:
        return "[LogLevel] APP_LOG_LEVEL 未設，使用預設 WARNING"
    name = raw.strip().upper()
    if isinstance(getattr(logging, name, None), int):
        return f"[LogLevel] {name} ok"
    return (
        f"⚠️ [LogLevel] APP_LOG_LEVEL={raw!r} 不是合法等級，已退回 WARNING"
        f"——檢查這個值有沒有跟別的變數黏在同一行"
    )


def log_flag_state(service: str, log) -> None:
    """啟動時印旗標快照，並比對跨服務一致性（API 與 analysis-worker 共用）。

    兩者是同一題的兩條執行路徑（同步串流 vs 背景任務）。Mixer 旗標只在一邊
    開，會變成「同一題行為不同」的鬼故事，而且兩邊各自看都正常——所以要主動
    比對並大聲說出來。任何失敗都不擋啟動。
    """
    try:
        log.info(flag_snapshot_line(service))
        store_service_snapshot(service)
        line = schema_version_line()
        (log.warning if "SchemaDrift" in line else log.info)(line)
        line = log_level_line()
        (log.warning if "LogLevel]" in line and "⚠️" in line else log.info)(line)
        for msg in unknown_flag_lines():
            log.warning(msg)
        for other, name, mine, theirs in check_cross_service_flags(service):
            log.warning(
                "⚠️ [FlagDivergence] %s=%s here (%s) but %s on %s — "
                "同一題走兩條路徑行為會不一致，請把兩個服務的 env 對齊",
                name, mine, service, theirs, other,
            )
    except Exception as exc:  # noqa: BLE001 — 旗標比對絕不可擋啟動
        log.warning("flag snapshot failed (non-fatal): %s", exc)


def schema_version_pair() -> tuple[str | None, str | None]:
    """(db_version, head_version)——worker 開機等 schema 就緒用（2026-09-05）。

    head 失敗回 ``(None, None)``、db 失敗回 ``(None, head)``：呼叫端用
    ``None`` 分辨「還不能判斷」與「已同步」，絕不把讀不到當成 ok。
    """
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[1]
        head = ScriptDirectory.from_config(
            Config(str(root / "alembic.ini"))
        ).get_current_head()
    except Exception:  # noqa: BLE001 — head 讀不到就沒有可比對的基準
        return (None, None)
    try:
        from core.database import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            db = row[0] if row else None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — db 讀不到＝還在起，呼叫端會重試
        return (None, head)
    return (db, head)


def schema_version_line() -> str:
    """啟動時印 DB 的 alembic 版本與程式碼期望的 head，不一致就大聲說。

    2026-09-05：production 的 alembic 卡在 c042 好幾週沒人發現。
    docker-entrypoint.sh 的遷移是 best-effort（設計正確——遷移失敗不該讓整個
    API crashloop），但它只印一行 WARNING 就繼續啟動 gunicorn，服務對外完全
    健康。唯一的訊號埋在沒人看的 log 裡。

    這一行讓「schema 落後」跟 FlagDivergence 一樣可 grep、一樣刺眼。
    """
    db, head = schema_version_pair()
    if head is None:
        return "[SchemaVersion] head 讀取失敗（非致命）"
    if db is None:
        return "[SchemaVersion] db 版本讀取失敗（非致命）"
    if db == head:
        return f"[SchemaVersion] db={db} head={head} ok"
    return (
        f"⚠️ [SchemaDrift] db={db} head={head} —— 遷移沒跑完，"
        "程式碼期望的欄位／主鍵可能不存在。查 entrypoint log 的 alembic 區段。"
    )
