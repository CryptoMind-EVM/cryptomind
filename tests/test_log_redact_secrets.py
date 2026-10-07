"""log 裡網址帶的金鑰要遮掉（2026-09-27：cron-worker 的 httpx INFO log 印出 FRED api_key）。"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import pytest

from config.logging_config import (
    SecretRedactionFilter,
    install_secret_redaction,
    redact_secrets,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "raw, secret",
    [
        (
            "HTTP Request: GET https://api.stlouisfed.org/fred/release/dates?release_id=10"
            "&api_key=654a625fcbc0&file_type=json \"HTTP/1.1 200 OK\"",
            "654a625fcbc0",
        ),
        ("GET https://api.etherscan.io/v2/api?chainid=8453&apikey=ABC123XYZ&module=account", "ABC123XYZ"),
        ("GET https://x.example/v1?key=AIzaSyD-secret_1 200", "AIzaSyD-secret_1"),
        ("GET /api/email/confirm?token=abcDEF123-_x HTTP/1.1", "abcDEF123-_x"),
        ("POST https://api.telegram.org/bot8849231142:AAH-secret_token/sendMessage", "AAH-secret_token"),
        ("Client error '401 Unauthorized' for url 'https://h.example/q?access_token=tok9'", "tok9"),
    ],
)
def test_redacts_secret_values(raw, secret):
    out = redact_secrets(raw)
    assert secret not in out
    assert "***" in out


def test_keeps_non_secret_parts():
    raw = "GET https://api.stlouisfed.org/fred/release/dates?release_id=10&api_key=654a&file_type=json"
    out = redact_secrets(raw)
    assert "release_id=10" in out and "file_type=json" in out
    assert "api_key=***" in out
    assert redact_secrets("nothing to hide here") == "nothing to hide here"


def _capture(logger_name: str, *args, exc: bool = False) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        if exc:
            try:
                raise RuntimeError("for url 'https://h.example/q?api_key=inexc123'")
            except RuntimeError:
                logger.exception(*args)
        else:
            logger.info(*args)
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    return stream.getvalue()


def test_filter_redacts_lazy_args_like_httpx():
    # httpx 用 %s 參數記網址：要在格式化之後遮
    out = _capture(
        "test.redact.args",
        'HTTP Request: %s %s "%s %d %s"',
        "GET",
        "https://api.stlouisfed.org/x?api_key=lazy999&a=1",
        "HTTP/1.1",
        200,
        "OK",
    )
    assert "lazy999" not in out and "api_key=***" in out and "a=1" in out


def test_filter_redacts_traceback_text():
    out = _capture("test.redact.exc", "boom", exc=True)
    assert "inexc123" not in out and "Traceback" in out


def test_install_is_idempotent_and_covers_httpx():
    install_secret_redaction()
    install_secret_redaction()
    httpx_filters = [
        f for f in logging.getLogger("httpx").filters if isinstance(f, SecretRedactionFilter)
    ]
    assert len(httpx_filters) == 1
    for handler in logging.getLogger().handlers:
        assert sum(isinstance(f, SecretRedactionFilter) for f in handler.filters) == 1


@pytest.mark.parametrize(
    "entry",
    [
        "api_server.py",
        "scripts/analysis_worker.py",
        "scripts/cron_calendar_sync.py",
        "scripts/cron_daily_brief.py",
        "scripts/cron_judgment_scores.py",
        "scripts/cron_onchain_sync.py",
        "scripts/cron_recompute_trust.py",
        "scripts/cron_wallet_monitor.py",
    ],
)
def test_every_logging_entry_point_installs_redaction(entry):
    """設了 logging 的進入點都要在設定後掛遮蔽（漏一支就會再印出金鑰）。"""
    source = (ROOT / entry).read_text(encoding="utf-8")
    assert "install_secret_redaction()" in source
    setup = max(source.find("logging.basicConfig("), source.find("setup_json_logging("))
    assert setup != -1
    assert source.find("install_secret_redaction()") > setup, "要在 logging 設定之後呼叫"


def test_no_other_entry_point_sets_up_logging_without_redaction():
    """新增的 cron／worker 腳本若自己 basicConfig，也要掛遮蔽。"""
    missing = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if re.search(r"^logging\.basicConfig\(", source, re.M) and path.name.startswith(
            ("cron_", "analysis_worker")
        ):
            if "install_secret_redaction()" not in source:
                missing.append(path.name)
    assert not missing, missing
