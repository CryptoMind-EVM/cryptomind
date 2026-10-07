"""後台「設定中心」（2026-09-27 DANNY：後台參數跟實際運作對不上，要能一眼看清楚）。

把散在各處的開關與數值集中成一份「現在實際是什麼」：

- 旗標：FLAG_REGISTRY 的執行期值＋分組（FLAG_GROUPS）＋依賴（FLAG_REQUIREMENTS）
  ——設定值是開、但缺環境變數或前置開關時，標出「實際是關的」與原因。
- 各服務一致性：api／analysis-worker／cron-worker 啟動（或每次跑）時寫進共用快取的快照。
- 數值參數：額度、價格。值一律從真正在用的 getter 讀（不是另存一份），所以不會跟程式不攏。

這裡只讀、不改任何東西。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional


def _config(attr: str) -> Callable[[], Any]:
    def getter():
        from core import config

        return getattr(config, attr)

    return getter


def _free_daily_chat_limit():
    from core.config import FREE_DAILY_CHAT_LIMIT
    from core.setting_overrides import int_param

    return int_param("FREE_DAILY_CHAT_LIMIT", FREE_DAILY_CHAT_LIMIT)


def _guest_daily_questions():
    from api.routers.guest import _daily_limit

    return _daily_limit()


def _guest_global_cap():
    from api.routers.guest import _global_cap

    return _global_cap()


def _platform_cloud_daily_limit():
    from core.agents.fallback import fallback_daily_limit

    return fallback_daily_limit()


def _premium_price(plan: str):
    def getter():
        from core.config import PREMIUM_USD_PRICES

        return PREMIUM_USD_PRICES[plan]

    return getter


# name -> (分組, 說明, 單位, getter, 環境變數名（None＝寫死在程式裡）)
PARAM_REGISTRY: dict = {
    "FREE_DAILY_CHAT_LIMIT": ("額度", "免費會員每日 AI 對話次數（進階會員不限）", "次／天", _free_daily_chat_limit, "FREE_DAILY_CHAT_LIMIT"),
    "GUEST_DAILY_QUESTIONS": ("額度", "訪客每人每日提問數", "題／天", _guest_daily_questions, "GUEST_DAILY_QUESTIONS"),
    "GUEST_GLOBAL_DAILY_CAP": ("額度", "全站訪客每日提問總上限", "題／天", _guest_global_cap, "GUEST_GLOBAL_DAILY_CAP"),
    "BYOK_FALLBACK_DAILY_LIMIT": ("額度", "沒綁金鑰的人使用平台「雲端」金鑰的每日上限（本地模型 CryptoMind Lite 不受限）", "次／天", _platform_cloud_daily_limit, "BYOK_FALLBACK_DAILY_LIMIT"),
    "PREMIUM_MONTHLY_USD": ("價格（USDC on Base）", "Premium 月費", "USD", _premium_price("premium_monthly"), "PREMIUM_MONTHLY_USD"),
    "PREMIUM_YEARLY_USD": ("價格（USDC on Base）", "Premium 年費", "USD", _premium_price("premium_yearly"), "PREMIUM_YEARLY_USD"),
    "FORUM_POST_FEE_USD": ("價格（USDC on Base）", "免費會員發文費（0＝免費；Premium 一律免費）", "USD", _config("FORUM_POST_FEE_USD"), "FORUM_POST_FEE_USD"),
    "FORUM_TIP_MIN_USD": ("價格（USDC on Base）", "打賞最低金額", "USD", _config("FORUM_TIP_MIN_USD"), "FORUM_TIP_MIN_USD"),
    "FORUM_TIP_MAX_USD": ("價格（USDC on Base）", "打賞最高金額", "USD", _config("FORUM_TIP_MAX_USD"), "FORUM_TIP_MAX_USD"),
    "FORUM_TIP_DEFAULT_USD": ("價格（USDC on Base）", "打賞預設金額", "USD", _config("FORUM_TIP_DEFAULT_USD"), None),
}

_KIND_LABEL = {"flag": "要先開", "env": "缺環境變數"}


def _unmet(name: str, values: dict) -> list:
    """這個旗標還缺什麼（空清單＝依賴都到位）。"""
    from core.feature_flags import FLAG_REQUIREMENTS

    out = []
    for kind, target in FLAG_REQUIREMENTS.get(name, ()):
        if kind == "flag" and not values.get(target):
            out.append(f"要先開 {target}")
        elif kind == "env" and not os.getenv(target, "").strip():
            out.append(f"缺環境變數 {target}")
        elif kind == "email_config":
            try:
                from core.email_brief.provider import missing_config

                out.extend(f"缺環境變數 {key}" for key in missing_config())
            except Exception:  # noqa: BLE001
                out.append("無法檢查寄信設定")
    return out


def _override_fields(name: str, overrides: dict) -> dict:
    from core.setting_overrides import is_overridable

    ov = overrides.get(name) or {}
    return {
        "overridable": is_overridable(name),
        "override": ov.get("value"),
        "override_by": ov.get("updated_by"),
        "override_at": ov.get("updated_at"),
    }


def _flag_rows(values: dict, snapshots: dict, overrides: dict) -> list:
    from core.feature_flags import CROSS_SERVICE_FLAGS, FLAG_GROUPS, FLAG_REGISTRY

    rows = []
    for group, names in FLAG_GROUPS.items():
        for name in names:
            _getter, default, note = FLAG_REGISTRY[name]
            value = values.get(name)
            unmet = _unmet(name, values) if value else []
            services = {
                svc: snap["flags"].get(name)
                for svc, snap in snapshots.items()
                if name in snap["flags"]
            }
            distinct = {v for v in services.values() if v is not None}
            env_raw: Optional[str] = os.getenv(name)
            extra = _override_fields(name, overrides)
            if extra["override"] is not None:
                # 所有服務讀同一份覆寫；快照是啟動時（或上次回報）的值，比較只會誤報
                services, distinct = {}, set()
            rows.append(
                {
                    "name": name,
                    "group": group,
                    "description": note,
                    "default": default.replace("*", ""),
                    "env": env_raw.strip() if env_raw is not None else None,
                    "value": value,
                    "effective": bool(value) and not unmet,
                    "unmet": unmet,
                    "requires": [
                        target or "寄信設定"
                        for _kind, target in _requirements(name)
                    ],
                    "services": services,
                    "consistent": len(distinct) <= 1,
                    "cross_service": name in CROSS_SERVICE_FLAGS,
                    **extra,
                }
            )
    return rows


def _requirements(name: str):
    from core.feature_flags import FLAG_REQUIREMENTS

    return FLAG_REQUIREMENTS.get(name, ())


def _param_rows(overrides: dict) -> list:
    from core.setting_overrides import OVERRIDABLE_PARAMS

    rows = []
    for name, (group, description, unit, getter, env_key) in PARAM_REGISTRY.items():
        try:
            value = getter()
        except Exception:  # noqa: BLE001
            value = None
        env_raw = os.getenv(env_key) if env_key else None
        rows.append(
            {
                "name": name,
                "group": group,
                "description": description,
                "unit": unit,
                "value": value,
                "env_key": env_key,
                "env": env_raw.strip() if env_raw is not None else None,
                "bounds": list(OVERRIDABLE_PARAMS[name]) if name in OVERRIDABLE_PARAMS else None,
                **_override_fields(name, overrides),
            }
        )
    return rows


def build_settings_center() -> dict:
    from core.feature_flags import all_flags, read_service_snapshots

    try:
        from core.setting_overrides import load_rows

        overrides = load_rows()
    except Exception:  # noqa: BLE001 — 讀不到覆寫表就當沒有（仍顯示實際值）
        overrides = {}
    values = all_flags()
    snapshots = read_service_snapshots()
    return {
        "flags": _flag_rows(values, snapshots, overrides),
        "params": _param_rows(overrides),
        "services": {svc: snap.get("at") for svc, snap in snapshots.items()},
    }
