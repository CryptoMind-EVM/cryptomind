"""Consent Gate 的拒絕／檢查失敗訊息要走 i18n，不可固定回中文。

舊碼是 `t("consent.high_risk_declined", …) if False else "<中文>"`——
key 不存在，就用 `if False` 永遠走中文分支，en／ru 使用者拒絕同意後
收到一句中文。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agents.manager import claw_loop
from core.i18n import SUPPORTED_LANGUAGES, t

KEYS = (
    "ui_messages.consent.high_risk_declined",
    "ui_messages.consent.high_risk_check_failed",
)


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_consent_messages_translated(key, lang):
    text = t(key, lang)
    assert text != key, f"{key} 缺 {lang} 譯文"
    if lang in ("en", "ru"):
        assert not any("一" <= ch <= "鿿" for ch in text), (
            f"{lang} 的 {key} 還是中文"
        )


def test_claw_loop_uses_consent_keys():
    src = Path(claw_loop.__file__).read_text(encoding="utf-8")
    assert "if False" not in src, "還有 `if False` 的死分支"
    for key in KEYS:
        assert f'"{key}"' in src, f"claw_loop 沒用 {key}"
    assert "使用者拒絕了高風險操作的同意請求" not in src
    assert "高風險操作的授權檢查未完成" not in src
