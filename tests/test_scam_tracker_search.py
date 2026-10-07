"""詐騙檢查頁：搜尋與舉報要吃 EVM／TON 地址（2026-09-24 正式站實測按了沒反應）。

根因：scam-tracker-i18n.js 的 patchApp() 用舊版覆寫了 handleSearch／handleSubmitReport，
只認 G 開頭（Stellar 式）地址，0x／EQ／UQ 全被當格式錯誤，健診 API 根本沒被呼叫；
原本的 scam-tracker.js 已經是鏈中立＋i18n，覆寫是多餘的。
另外舉報表單把地址整串 toUpperCase()，0x 會變 0X、EQ/UQ 的大小寫也被毀掉。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "web" / "scam-tracker" / "js"


def test_i18n_patch_does_not_override_search_or_submit():
    src = (JS / "scam-tracker-i18n.js").read_text(encoding="utf-8")
    assert "app.handleSearch =" not in src
    assert "app.handleSubmitReport =" not in src


def test_submit_does_not_uppercase_whole_address():
    src = (JS / "scam-tracker.js").read_text(encoding="utf-8")
    body = src[src.index("async handleSubmitReport(") :]
    body = body[: body.index("const scamType")]
    assert "toUpperCase()" not in body, "0x→0X 會讓 EVM 地址驗證失敗"
    assert "normalizeWalletInput(" in body


def _extract(src: str, name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^}}", src, re.S | re.M)
    assert m, name
    return m.group(0)


@pytest.mark.parametrize(
    "raw, ok",
    [
        ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", True),
        ("  0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 ", True),
        ("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs", True),
        ("UQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs", True),
        ("0x123", False),
        ("hello", False),
    ],
)
def test_normalize_then_validate(raw, ok):
    src = (JS / "scam-tracker.js").read_text(encoding="utf-8")
    code = "\n".join(
        [
            _extract(src, "isValidWalletAddress"),
            _extract(src, "normalizeWalletInput"),
            f"console.log(JSON.stringify(isValidWalletAddress(normalizeWalletInput({json.dumps(raw)}))));",
        ]
    )
    out = subprocess.run(
        ["node", "-e", code], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    assert json.loads(out) is ok


@pytest.mark.parametrize(
    "tx, ok",
    [
        ("a" * 64, True),
        ("0x" + "b" * 64, True),
        ("te6ccgEBAQEAJAAAQ4AFuC0WnDyUFHcbCg" + "Ab+/=_-x", True),  # TON base64 類
        ('<img src=x onerror="a()">' + "x" * 20, False),
        ("short", False),
    ],
)
def test_report_tx_hash_charset(tx, ok):
    from pydantic import ValidationError

    from api.routers.scam_tracker.models import ScamReportCreate

    base = dict(
        scam_wallet_address="0x" + "1" * 40,
        reporter_wallet_address="0x" + "2" * 40,
        scam_type="phishing",
        description="x" * 30,
    )
    if ok:
        assert ScamReportCreate(**base, transaction_hash=tx).transaction_hash
    else:
        with pytest.raises(ValidationError):
            ScamReportCreate(**base, transaction_hash=tx)
