"""開機時的環境變數健檢守衛。

2026-09-06 對照線上 Zeabur env 時抓到兩類問題，**兩類都不噴錯**：

1. 面板上兩個變數被貼成同一行：
   ``APP_LOG_LEVEL=INFOAUTH_LOCKOUT_ENABLED=true``
   → ``getattr(logging, ...)`` 找不到就靜靜退回 WARNING。你以為開了 INFO，
     實際上 INFO 全部看不到；同一行還吃掉了 AUTH_LOCKOUT_ENABLED。
2. 已移除的旗標還留在面板上（``AI_STUDIO_ENABLED``，2026-09-04 移除）
   → 讓人以為某個功能開著。

共通點是「設錯了不會有人知道」。這組守衛讓它們在開機 log 上大聲說出來。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


# ── APP_LOG_LEVEL ───────────────────────────────────────────────────────


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "warning", "Error", "CRITICAL"])
def test_valid_log_levels_are_quiet(level, monkeypatch):
    from core.feature_flags import log_level_line

    monkeypatch.setenv("APP_LOG_LEVEL", level)
    assert "⚠️" not in log_level_line()


def test_two_vars_glued_together_is_flagged(monkeypatch):
    """線上真的發生過的那一行，原封不動。"""
    from core.feature_flags import log_level_line

    monkeypatch.setenv("APP_LOG_LEVEL", "INFOAUTH_LOCKOUT_ENABLED=true")
    line = log_level_line()
    assert "⚠️" in line
    assert "退回 WARNING" in line, "要講出實際後果，不只是說值不合法"


def test_unset_log_level_is_not_an_error(monkeypatch):
    from core.feature_flags import log_level_line

    monkeypatch.delenv("APP_LOG_LEVEL", raising=False)
    assert "⚠️" not in log_level_line()


def test_lowercase_and_whitespace_are_tolerated(monkeypatch):
    """`  info ` 是合法的，不該誤報——誤報會讓人開始忽略警告。"""
    from core.feature_flags import log_level_line

    monkeypatch.setenv("APP_LOG_LEVEL", "  info ")
    assert "⚠️" not in log_level_line()


def test_non_level_logging_attribute_is_still_rejected(monkeypatch):
    """logging.BASIC_FORMAT 是屬性但不是等級——檢查不能只看存不存在。

    案例刻意選 BASIC_FORMAT 而不是 getLogger：值會先 upper()，``GETLOGGER``
    本來就不是屬性，用它測等於什麼都沒測（第一版就是這樣，突變驗證才發現）。
    """
    import logging

    assert isinstance(getattr(logging, "BASIC_FORMAT"), str), "前提變了就換一個案例"

    from core.feature_flags import log_level_line

    monkeypatch.setenv("APP_LOG_LEVEL", "BASIC_FORMAT")
    assert "⚠️" in log_level_line()


# ── 未知／已移除的旗標 ──────────────────────────────────────────────────


def test_removed_flag_still_set_is_flagged(monkeypatch):
    from core.feature_flags import unknown_flag_lines

    monkeypatch.setenv("AI_STUDIO_ENABLED", "1")
    assert any("AI_STUDIO_ENABLED" in line for line in unknown_flag_lines())


def test_missing_d_typo_is_flagged(monkeypatch):
    """ROUTER_ENABLE（漏一個 D）——env_flag 讀不到就靜靜用預設。"""
    from core.feature_flags import unknown_flag_lines

    monkeypatch.setenv("ROUTER_ENABLE", "1")
    assert any("ROUTER_ENABLE" in line for line in unknown_flag_lines())


def test_registered_flags_are_not_flagged(monkeypatch):
    from core.feature_flags import FLAG_REGISTRY, unknown_flag_lines

    for name in list(FLAG_REGISTRY)[:5]:
        monkeypatch.setenv(name, "1")
    lines = unknown_flag_lines()
    for name in list(FLAG_REGISTRY)[:5]:
        assert not any(name in line for line in lines), f"{name} 是已登記旗標，不該被警告"


def test_non_flag_env_is_ignored(monkeypatch):
    """金鑰、URL、閾值不在管轄範圍——掃了只會製造雜訊，雜訊會讓人忽略警告。"""
    from core.feature_flags import unknown_flag_lines

    monkeypatch.setenv("SOME_RANDOM_API_KEY", "x")
    monkeypatch.setenv("ROUTER_TIMEOUT_SECONDS", "6")
    lines = unknown_flag_lines()
    assert not any("SOME_RANDOM_API_KEY" in line or "ROUTER_TIMEOUT" in line for line in lines)


# ── 部署設定（自架 docker compose）─────────────────────────────────────


def _prod_compose() -> dict:
    import yaml

    return yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )




def test_no_zeabur_build_configs_left():
    """zbpack.*.json 只有 Zeabur 會讀；自架之後它們不影響任何建置。"""
    leftovers = sorted(p.name for p in ROOT.glob("zbpack*.json"))
    assert not leftovers, f"Zeabur 專用設定檔已無作用：{leftovers}"


# ── .env.example 是唯一的完整清單 ────────────────────────────────────────
#
# 2026-09-25 部署盤點時，程式讀的環境變數有 ~90 個不在 .env.example（DB pool、
# 背景工作開關、TON 定價錨點、LINE、SMTP…），要設什麼只能翻程式碼。這條守衛讓
# 「程式新讀一個 env 卻沒寫進 .env.example」直接紅。
#
# 掃描範圍＝會進 app image 並在正式站執行的程式碼：core/ api/ bot/ utils/
# analysis/ alembic/、api_server.py、gunicorn.conf.py，加上 .dockerignore 白名單
# 放行的 scripts（cron／worker／env-check）。其餘 scripts/ 是本機工具，它們吃的
# env（BASE_URL、PROD_DATABASE_URL…）是執行參數不是站台設定。

_RUNTIME_DIRS = ("core", "api", "bot", "utils", "analysis", "alembic")
_RUNTIME_FILES = ("api_server.py", "gunicorn.conf.py")
_NAME = r"([A-Z][A-Z0-9_]{2,})"
_READ_PATTERNS = (
    # os.getenv("X")／os.environ.get("X")／env.get("X")／env_flag("X")／_env("X")／_int_env("X")…
    re.compile(
        r"(?:\bgetenv|\benviron\.get|\benviron\.setdefault|\benv\.get|\benv_flag"
        r"|\b_flag|\b_env\w*|\b\w+_env)\(\s*[rbuf]?[\"']" + _NAME + r"[\"']"
    ),
    re.compile(r"\benviron\[\s*[\"']" + _NAME + r"[\"']\s*\]"),
    # resolve_tool_key(..., official_env="X")
    re.compile(r"\bofficial_env\s*=\s*[\"']" + _NAME + r"[\"']"),
)
# PROVIDER_REGISTRY 的 "api_key_envs": ["A", "B"]
_KEY_LIST = re.compile(r"[\"']api_key_envs[\"']\s*:\s*\[([^\]]*)\]")

# 程式確實讀了、但刻意不寫進 .env.example 的。每一項都要有理由；
# test_exclusions_are_still_read 確保程式碼刪掉後這裡也跟著刪。
_NOT_DOCUMENTED = {
    # utils/settings.py 的 DEBUG_MODE 沒有任何執行期程式在讀——寫進去只會讓人以為有用
    "DEBUG": "utils/settings.py",
    # 同上；只剩 tests/ 裡幾支手動互動腳本會看 Settings.ENABLE_MANAGER_V2
    "ENABLE_MANAGER_V2": "utils/settings.py",
    # core/config.py 標註「已停用」的常數，讀了沒人用
    "ETHERSCAN_API_KEY": "core/config.py",
    "WHALE_ALERT_API_KEY": "core/config.py",
    # core/tools/us_stock_tools.py 的 `if __name__ == "__main__"` 示範
    "US_STOCK_SAMPLE_SYMBOL": "core/tools/us_stock_tools.py",
    # pytest 自己設的；行情預熱在測試裡不去打外部 API（不是給部署設定的）
    "PYTEST_CURRENT_TEST": "api/market_warmup.py",
}


def _runtime_sources() -> list[Path]:
    files = [p for d in _RUNTIME_DIRS for p in (ROOT / d).rglob("*.py")]
    files += [ROOT / f for f in _RUNTIME_FILES]
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    files += [
        ROOT / m for m in re.findall(r"^!(scripts/[\w.-]+\.py)\s*$", dockerignore, re.M)
    ]
    return files


def _env_reads() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _runtime_sources():
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(ROOT))
        names = [n for pat in _READ_PATTERNS for n in pat.findall(text)]
        for block in _KEY_LIST.findall(text):
            names += re.findall(r"[\"']" + _NAME + r"[\"']", block)
        for name in names:
            found.setdefault(name, rel)
    return found


def _documented(path: str) -> set[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    # 「KEY=」形式（含註解掉的 `# KEY=`）才算有文件，散文裡順口提到不算
    return set(re.findall(r"^#?\s*" + _NAME + r"=", text, re.M))


def test_scanner_sees_known_reads():
    """掃描器本身的健全性：各種讀法都要抓得到，不然下面那條會空轉。"""
    reads = _env_reads()
    for name in (
        "DATABASE_URL",  # os.getenv
        "LOCAL_LLAMA_BASE_URL",  # _env(...)
        "POSTGRESQL_HOST",  # env.get(...)
        "LOCAL_LLAMA_API_KEY",  # api_key_envs 清單
        "NEWSAPI_KEY",  # official_env=
        "DAILY_BRIEF_CONCURRENCY",  # 白名單 scripts/cron_daily_brief.py
        "GUNICORN_ACCESSLOG",  # gunicorn.conf.py
    ):
        assert name in reads, f"掃描器漏抓 {name}"
    assert len(reads) > 150




def test_exclusions_are_still_read():
    reads = _env_reads()
    stale = sorted(n for n in _NOT_DOCUMENTED if n not in reads)
    assert not stale, f"這些排除項程式已經不讀了，從 _NOT_DOCUMENTED 拿掉：{stale}"




