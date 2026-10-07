from __future__ import annotations

import re
from pathlib import Path

from api_server import app

ROOT = Path(__file__).resolve().parents[1]


def test_safety_and_guard_api_routes_are_not_mounted():
    # 論壇檢舉按鈕只需要「送出檢舉」這一條（2026-09-26 論壇開放前恢復）；
    # 社群投票、申訴等其餘 governance 路由仍不掛
    forum_report = ("/api/governance/reports", frozenset({"POST"}))
    routes = {
        (route.path, frozenset(getattr(route, "methods", None) or ()))
        for route in app.routes
        if hasattr(route, "path")
    }
    disabled_prefixes = (
        "/api/scam-tracker",
        "/api/governance",
        "/api/guard",
        "/v1/guard",
    )

    assert forum_report in routes
    assert not any(
        path.startswith(disabled_prefixes)
        for path, methods in routes
        if (path, methods) != forum_report
    )


def test_safety_is_hidden_and_removed_from_spa_routes():
    nav_config = (ROOT / "web/js/nav-config.js").read_text(encoding="utf-8")
    spa = (ROOT / "web/js/spa.js").read_text(encoding="utf-8")
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")

    # 2026-08-18 死代碼清理：safety nav 項目整支移除（詐騙檢測改由獨立頁
    # web/scam-tracker/ 提供）——比早期「hidden 保留」更強的移除斷言。
    assert re.search(r"\{\s*id:\s*'safety'", nav_config) is None
    # v20 清除舊 safety 偏好；v21 加入 journal（2026-08-22）——版本只會往上
    assert re.search(r"PREFERENCES_VERSION: \d+", nav_config) is not None
    assert int(re.search(r"PREFERENCES_VERSION: (\d+)", nav_config).group(1)) >= 20

    valid_tabs = re.search(r"var VALID_TABS = \[(.*?)\];", spa, flags=re.DOTALL)
    assert valid_tabs is not None
    assert "'safety'" not in valid_tabs.group(1)
    assert "safety: () =>" not in spa
    assert 'id="safety-tab"' not in index
