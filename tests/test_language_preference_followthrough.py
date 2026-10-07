"""使用者語言偏好要一路生效（DANNY 2026-09-13：「agent 或早報是否能回答使用者語言偏好」）。

三個缺口：(1) 網頁聊天看不出語言的短句一律回英文；(2) 從沒手動切語言的人 users.language
永遠是 NULL，Telegram 與早報只能猜；(3) Telegram 第一次登入沒把客戶端語言種下。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent


def test_node_message_language_assertions():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "message_language.mjs")],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_chat_uses_shared_detector_with_ui_language():
    src = (REPO / "web" / "js" / "chat-analysis.js").read_text(encoding="utf-8")
    assert "from './message-language.js'" in src
    assert "detectMessageLanguageFor(text, window.I18n?.getLanguage?.()" in src
    assert "return 'en';\n}" not in src.split("window.detectMessageLanguage")[0], (
        "舊的「其餘一律 en」分支不該留在 chat-analysis.js"
    )


def test_i18n_persists_initial_language_on_first_login():
    src = (REPO / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "if (!serverLang && data?.user) {" in src
    assert "persistLanguageToServer(i18n.language);" in src
    assert "if (baseLang === 'ru') return 'ru';" in src


@pytest.mark.parametrize(
    "code, expected",
    [
        ("zh-hant", "zh-TW"),
        ("zh-TW", "zh-TW"),
        ("zh", "zh-TW"),
        ("zh-hans", "zh-CN"),
        ("zh-CN", "zh-CN"),
        ("zh_SG", "zh-CN"),
        ("ru", "ru"),
        ("ru-RU", "ru"),
        ("en", "en"),
        ("ja", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_language_from_client_code(code, expected):
    from core.i18n import language_from_client_code

    assert language_from_client_code(code) == expected


def test_telegram_login_seeds_language_for_new_accounts():
    src = (REPO / "api" / "routers" / "user.py").read_text(encoding="utf-8")
    body = src.split("async def telegram_login(")[1].split("_ME_CACHE: TTLCache")[0]
    assert 'language_from_client_code(tg_user.get("language_code"))' in body
    assert 'set_user_language, result["user_id"], seed_lang' in body
    # 只在「第一次建帳號」那條分支，既有帳號不能被客戶端語言蓋掉
    first_time = body.split("# First time: create a Telegram-native account")[1]
    assert "seed_lang" in first_time
    existing = body.split("# First time: create a Telegram-native account")[0]
    assert "seed_lang" not in existing
