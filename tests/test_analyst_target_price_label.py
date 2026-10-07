"""分析師目標價餵給 AI 時要標明是「第三方分析師預估」，不是本平台的預測（2026-10-05，交接文件「雜項」）。

資料照舊給（沒有拿掉），只是標籤講清楚：產品原則是 AI 只做資料分析、不下買賣指令（analysis_not_advice），
把外部供應商的目標價寫成「分析師目標價」容易被 AI 當成自己的結論複述。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROUTERS = ("twstock", "hkstock", "jpstock", "krstock", "astock", "instock")
REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ROUTERS)
def test_target_price_is_labelled_as_third_party(name):
    src = (REPO / f"api/routers/{name}.py").read_text(encoding="utf-8")
    assert "第三方分析師預估目標價（非本平台預測）: {tp_str}" in src
    assert "分析師目標價:" not in src, "不要退回沒有說明來源的舊標籤"
    assert "{tp_str}" in src, "資料本身不拿掉，只改標籤"
