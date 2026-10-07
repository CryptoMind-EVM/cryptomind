"""開機前環境變數健檢（scripts/check_env_sanity.py）的守衛。

2026-09-06 同一個錯誤在幾小時內犯了兩次：

    APP_LOG_LEVEL=INFOAUTH_LOCKOUT_ENABLED＝true    靜默：log 等級退回 WARNING、
                                                    AUTH_LOCKOUT_ENABLED 失效
    WEB_CONCURRENCY=1AUTH_LOCKOUT_ENABLED＝true      致命：gunicorn 設定解析階段
                                                    ValueError，**停機約一小時**

第二次證明了 #669 的守衛位置不夠早——它住在 app lifespan，而 gunicorn 在那
之前就死了。所以這層檢查前移到 docker-entrypoint.sh。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_env_sanity.py"


def _run(env: dict) -> subprocess.CompletedProcess:
    base = {"PATH": "/usr/bin:/bin"}
    base.update(env)
    # Windows（cp950）：子程序預設用系統碼頁「寫出」，而且我們這邊也解不開——兩邊都要 UTF-8
    # （PYTHONUTF8 管子程序輸出，encoding 管我們讀；少一邊 r.stderr 就是亂碼或 None）。
    base = {**base, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=base,
        cwd=ROOT,
    )


# ── 兩次真實事故 ────────────────────────────────────────────────────────


def test_first_incident_log_level_glued_is_warned():
    r = _run({"APP_LOG_LEVEL": "INFOAUTH_LOCKOUT_ENABLED＝true"})
    assert "APP_LOG_LEVEL" in r.stderr
    assert r.returncode == 0, "非數值欄位只該警告，不該多製造一個擋開機的關卡"


def test_second_incident_web_concurrency_glued_is_fatal():
    """這個值無論如何都會讓 gunicorn 崩潰，早點炸並說清楚才有用。"""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("ces", SCRIPT)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)

    r = _run({"WEB_CONCURRENCY": "1AUTH_LOCKOUT_ENABLED＝true"})
    assert r.returncode == mod.EXIT_BAD_ENV
    assert "WEB_CONCURRENCY" in r.stderr
    assert "整數" in r.stderr


def test_bad_env_exit_code_does_not_collide_with_python_own_codes():
    """python 自己會用 1（未捕捉例外）與 2（檔案打不開）。

    撞號的話「腳本沒進 image」會被 entrypoint 誤判成「env 壞掉」而擋住開機
    ——這支存在的目的就是防停機，自己變成停機來源最糟。實測過：
    ``python scripts/does_not_exist.py`` 回 2、``raise RuntimeError`` 回 1。
    """
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("ces", SCRIPT)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.EXIT_BAD_ENV not in (0, 1, 2)


def test_entrypoint_only_blocks_on_the_dedicated_exit_code():
    """其他非零＝檢查本身壞了，只警告不擋。"""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("ces", SCRIPT)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)

    text = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert f'-eq {mod.EXIT_BAD_ENV} ]' in text, "entrypoint 認的碼要跟腳本一致"
    assert "略過" in text or "WARNING" in text, "其他非零要走警告路徑不是 exit"


# ── 黏行的三種特徵 ──────────────────────────────────────────────────────


def test_fullwidth_equals_is_detected():
    r = _run({"SOME_SETTING": "value＝other"})
    assert "SOME_SETTING" in r.stderr


def test_newline_inside_value_is_detected():
    r = _run({"SOME_SETTING": "value\nOTHER_VAR=x"})
    assert "SOME_SETTING" in r.stderr


def test_embedded_env_var_name_is_detected():
    """半形等號黏行沒有全形線索，只能靠「值裡夾著別的變數名」認出來。"""
    r = _run({"APP_LOG_LEVEL": "INFOAUTH_LOCKOUT_ENABLED=true", "AUTH_LOCKOUT_ENABLED": "true"})
    assert "APP_LOG_LEVEL" in r.stderr


# ── 不可以誤報 ──────────────────────────────────────────────────────────


def test_clean_env_passes_quietly():
    r = _run({"WEB_CONCURRENCY": "1", "APP_LOG_LEVEL": "INFO", "ENVIRONMENT": "production"})
    assert r.returncode == 0
    assert "⚠️" not in r.stderr


def test_urls_with_colons_are_not_flagged():
    """半形冒號在 URL / DSN 裡到處都是，抓它等於全部誤報。"""
    r = _run({
        "DATABASE_URL": "postgresql://u:p@host:5432/db",
        "REDIS_URL": "redis://:pw@host:6379",
        "LANGFUSE_BASE_URL": "https://us.cloud.langfuse.com",
    })
    assert r.returncode == 0
    assert "⚠️" not in r.stderr


def test_short_var_names_do_not_cause_false_positives():
    """短名字（如 PATH）出現在別的值裡很正常，不能當黏行證據。

    只斷言 returncode 是不夠的——警告不影響 exit code，那樣寫等於什麼都沒測
    （第一版就是這樣，突變驗證才發現）。要看的是 stderr 有沒有警告。
    """
    r = _run({"PATH": "/usr/bin", "SOMETHING": "/usr/bin:/opt/PATH/bin"})
    assert r.returncode == 0
    assert "SOMETHING" not in r.stderr, "短名字誤報會讓人開始忽略這個檢查"


def test_empty_values_are_skipped():
    r = _run({"WEB_CONCURRENCY": "", "APP_LOG_LEVEL": ""})
    assert r.returncode == 0
    assert "⚠️" not in r.stderr


# ── 不外洩 ──────────────────────────────────────────────────────────────


def test_secret_values_are_truncated_in_output():
    """訊息會進 Zeabur log，不能把完整金鑰印出來。"""
    secret = "sk-super-secret-key-that-should-not-appear＝glued"
    r = _run({"SOME_API_KEY_ENABLED": secret})
    assert secret not in r.stderr
    assert "sk-super-sec" in r.stderr, "還是要印前綴，否則認不出是哪一行"


# ── entrypoint 有接上 ───────────────────────────────────────────────────


def test_entrypoint_runs_the_check_before_gunicorn():
    """跑在 gunicorn 之後就沒意義了——gunicorn 設定解析階段就會先炸。"""
    text = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    check_at = text.find("check_env_sanity.py")
    gunicorn_at = text.find("exec gunicorn")
    assert check_at != -1, "entrypoint 沒有呼叫健檢"
    assert gunicorn_at != -1
    assert check_at < gunicorn_at


def test_gunicorn_config_names_the_variable_on_failure():
    """原本只丟 'invalid literal for int()'，看不出是哪個環境變數。"""
    text = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
    assert "WEB_CONCURRENCY=" in text and "不是整數" in text
