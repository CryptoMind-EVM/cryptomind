from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LOCALES = ("en", "zh-TW", "zh-CN", "ru")

REQUIRED_GUARD_KEYS = {
    "description",
    "apiReference",
    "handlesTitle",
    "handlesDescription",
    "externalExecutionTitle",
    "externalExecutionDescription",
    "myClients",
    "manage",
    "signInToManage",
    "controlCenter",
    "controlCenterDescription",
    "createClient",
    "clientNamePlaceholder",
    "create",
    "clients",
    "refresh",
    "apiKeys",
    "generateKey",
    "copyKey",
    "versionedPolicy",
    "publishPolicy",
    "recentDecisions",
    "signedReceipts",
    "loadingClients",
    "noClients",
    "clientCreated",
    "policyPublished",
}


def _guard_translations(locale: str) -> dict[str, str]:
    payload = json.loads(
        (WEB / "js" / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
    )
    return payload.get("guard", {})


def test_guard_namespace_exists_in_every_supported_locale():
    namespaces = {locale: _guard_translations(locale) for locale in LOCALES}
    expected_keys = set(namespaces["en"])

    assert REQUIRED_GUARD_KEYS <= expected_keys
    assert all(set(namespace) == expected_keys for namespace in namespaces.values())
    assert all(
        isinstance(value, str) and value.strip()
        for namespace in namespaces.values()
        for value in namespace.values()
    )


