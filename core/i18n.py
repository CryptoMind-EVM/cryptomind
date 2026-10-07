"""
後端輕量 i18n 模組。

仿前端 ``web/js/i18n/*.json`` 的 JSON-based 模式：所有翻譯放在
``core/i18n/*.json``，每個檔案是一個 namespace，4 語齊全（zh-TW/zh-CN/en/ru）。

使用方式
--------
.. code-block:: python

    from core.i18n import t

    # 基本
    msg = t("errors.analysis.message_too_long", "zh-CN")
    # => "消息过长..."

    # 帶變數插值（{var} placeholder）
    msg = t("ui.timeout", "en", seconds=30)
    # => "Execution timeout: exceeded 30 seconds..."

    # 用 unknown key → 退回 key 字串本身並 log warning（不爆）
    msg = t("typo.key", "zh-TW")
    # => "typo.key"  + logger.warning

設計選擇
--------
- **不用 gettext / babel**：避免編譯 .mo 檔、不必學新工具鏈
- **不用資料庫**：JSON 是純靜態資料，部署簡單
- **單例懶載入**：第一次 ``t()`` 才讀檔，後續走 cache
- **fallback 鏈**：精確 → zh-TW（主市場）→ key 本身

治理
----
``tests/test_i18n_governance.py`` 強制每個 namespace JSON 4 語齊全、
key 一致、無空值。新增語言或 namespace 必須同步更新該 test。

ru 為機翻，JSON 內標 ``"_todo_native_review": true``，正式 release ru 前
需母語者 review。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 支援語言清單（與 api/routers/user.py:_SUPPORTED_LANGUAGES 對齊）
SUPPORTED_LANGUAGES = ("zh-TW", "zh-CN", "en", "ru")
DEFAULT_LANGUAGE = "zh-TW"  # 主市場；完全未知語言時 fallback 到此

# namespace → { lang → { key → text } }
_translations: Dict[str, Dict[str, Dict[str, str]]] = {}
_loaded: bool = False

# {var} placeholder pattern
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_I18N_DIR = Path(__file__).parent / "i18n"


def _load() -> None:
    """懶載入所有 namespace JSON。重複呼叫只載一次。"""
    global _loaded
    if _loaded:
        return
    if not _I18N_DIR.exists():
        logger.warning("i18n directory not found: %s", _I18N_DIR)
        _loaded = True
        return
    for json_file in _I18N_DIR.glob("*.json"):
        namespace = json_file.stem
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 預期結構：{ "lang_code": { "dotted.key": "text" } }
            if isinstance(data, dict):
                _translations[namespace] = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load i18n namespace %s: %s", namespace, exc)
    _loaded = True


def _resolve_namespace_key(key: str) -> tuple[str, str]:
    """把 dotted key 切成 (namespace, sub_key)。

    ``errors.auth.invalid`` → (``errors``, ``auth.invalid``)
    若開頭不是已知 namespace，回 (預設 namespace, 原始 key)。
    """
    parts = key.split(".", 1)
    if len(parts) == 2 and parts[0] in _translations:
        return parts[0], parts[1]
    # 找哪個 namespace 含這個 key
    for ns, langs in _translations.items():
        for _lang, kv in langs.items():
            if key in kv:
                return ns, key
    return "", key


def _lookup(namespace: str, sub_key: str, lang: str) -> str | None:
    """在指定 namespace 內查 (sub_key, lang)。fallback 鏈：lang → zh-TW。"""
    if namespace not in _translations:
        return None
    ns_data = _translations[namespace]
    # 精確匹配
    text = ns_data.get(lang, {}).get(sub_key)
    if text:
        return text
    # fallback 到 zh-TW
    if lang != DEFAULT_LANGUAGE:
        text = ns_data.get(DEFAULT_LANGUAGE, {}).get(sub_key)
        if text:
            return text
    return None


def t(key: str, lang: str | None = None, **vars: Any) -> str:
    """翻譯查詢。

    Args:
        key: dotted key，例如 ``"errors.auth.invalid_credentials"``。
            開頭段必須對應 ``core/i18n/<segment>.json`` 檔名。
        lang: 語言代碼（``zh-TW`` / ``zh-CN`` / ``en`` / ``ru``）。
            ``None`` 或未知 → fallback 到 ``DEFAULT_LANGUAGE``。
        **vars: 變數插值，例如 ``t("hello", "en", name="Alice")``
            對應 ``"hello": "Hello, {name}!"``。

    Returns:
        翻譯後字串。找不到 key 時回 key 本身並 log warning（不拋例，
        避免 i18n 失敗擋住主流程）。

    Examples:
        >>> t("errors.generic", "zh-TW")
        '...'
        >>> t("ui.greeting", "en", user="Alice")
        'Hello, Alice!'
    """
    _load()

    # lang 預設 + 未知 fallback
    if not lang or lang not in SUPPORTED_LANGUAGES:
        if lang and lang.lower().startswith("zh"):
            # zh-HK / zh-SG 等 → 預設繁中
            lang = DEFAULT_LANGUAGE
        else:
            lang = DEFAULT_LANGUAGE

    namespace, sub_key = _resolve_namespace_key(key)
    text = _lookup(namespace, sub_key, lang)

    if text is None:
        logger.warning("i18n key not found: %r (lang=%s)", key, lang)
        return key

    # 變數插值
    if vars:

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            value = vars.get(var_name, match.group(0))
            return str(value)

        text = _PLACEHOLDER_RE.sub(_replace, text)

    return text


def language_from_client_code(code: str | None) -> str:
    """客戶端語言代碼（Telegram ``language_code``、瀏覽器 ``navigator.language``）
    → 支援語言。zh-CN／zh-Hans／zh-SG → zh-CN，其他 zh → zh-TW，ru → ru，其餘 en。
    給「第一次登入、還沒有偏好」時當種子用；之後以 users.language 為準。"""
    low = (code or "").strip().lower().replace("_", "-")
    if not low:
        return "en"
    if low.startswith("zh"):
        return "zh-CN" if any(x in low for x in ("cn", "hans", "sg")) else "zh-TW"
    if low.startswith("ru"):
        return "ru"
    return "en"


def get_supported_languages() -> tuple[str, ...]:
    """回傳支援語言清單（與 ``SUPPORTED_LANGUAGES`` 同步）。"""
    return SUPPORTED_LANGUAGES


def list_namespaces() -> list[str]:
    """列出已載入的 namespace（除錯 / 測試用）。"""
    _load()
    return sorted(_translations.keys())


def reload_for_tests() -> None:
    """強制重新載入（測試用，避免 module cache 干擾）。"""
    global _loaded
    _translations.clear()
    _loaded = False
    _load()


__all__ = [
    "t",
    "SUPPORTED_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "get_supported_languages",
    "list_namespaces",
    "reload_for_tests",
]
