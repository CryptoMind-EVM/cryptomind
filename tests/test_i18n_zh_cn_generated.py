"""zh-CN 詞表由 zh-TW 自動生成（2026-09-25 起多語系改 zh-TW 單一來源）。

scripts/i18n/gen_zh_cn.mjs：OpenCC(twp→cn) ＋ scripts/i18n/zh-CN.phrases.json 的大陸用詞表
＋ overrides 例外。手改 zh-CN.json 或改了 zh-TW 沒重跑，這裡會紅：跑 `npm run i18n:zh-cn`。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _node_ready():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    if not (REPO / "node_modules" / "opencc-js").exists():
        pytest.fail("缺 node_modules/opencc-js——先跑 npm ci（devDependency）")


def test_zh_cn_is_generated_from_zh_tw():
    _node_ready()
    res = subprocess.run(
        ["node", "scripts/i18n/gen_zh_cn.mjs", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stderr or res.stdout


def test_phrase_table_is_well_formed():
    table = json.loads(
        (REPO / "scripts/i18n/zh-CN.phrases.json").read_text(encoding="utf-8")
    )
    for section in ("pre", "phrases"):
        for pair in table.get(section, []):
            assert isinstance(pair, list) and len(pair) == 2 and all(pair), pair
    # pre 可以左右相同（純保護：例如「工具」防 OpenCC 把「更多工具」拆成「多工」）；phrases 不行
    for pair in table.get("phrases", []):
        assert pair[0] != pair[1], pair


def test_generator_fixes_known_opencc_misfires():
    """OpenCC 單獨轉會錯的詞，套了用詞表之後要是大陸慣用說法。"""
    _node_ready()
    probe = REPO / "scripts/i18n/_probe.mjs"
    # 直接用生成腳本裡真正的 toCN（含保護詞佔位符），不要在測試裡重寫一份轉換邏輯
    probe.write_text(
        "import { toCN } from './gen_zh_cn.mjs';\n"
        "const out = {};\n"
        "for (const s of JSON.parse(process.argv[2])) out[s] = toCN(s);\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    try:
        samples = [
            "複製",
            "登出",
            "離線中",
            "預設值",
            "執行",
            "查詢",
            "個人資料",
            "「引號」",
            "帳號",
            "RSI/MACD/布林/均線",
            "唯讀",
            "通膨、升息、聯準會",
            "本益比、股價淨值比",
            "K 線型態",
            "滑價",
        ]
        res = subprocess.run(
            ["node", str(probe), json.dumps(samples, ensure_ascii=False)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    finally:
        probe.unlink(missing_ok=True)
    out = json.loads(res.stdout)
    assert out["複製"] == "复制"
    assert out["登出"] == "退出登录"
    assert out["離線中"] == "离线中"
    assert out["預設值"] == "默认值"
    assert out["執行"] == "执行"
    assert out["查詢"] == "查询"
    assert out["個人資料"] == "个人资料"
    assert out["「引號」"] == "“引号”"
    assert out["帳號"] == "账号"
    # #849 review：布林（Bollinger）不可變布尔（Boolean）；台灣財經用詞轉大陸說法
    assert out["RSI/MACD/布林/均線"] == "RSI/MACD/布林/均线"
    assert out["唯讀"] == "只读"
    assert out["通膨、升息、聯準會"] == "通胀、加息、美联储"
    assert out["本益比、股價淨值比"] == "市盈率、市净率"
    assert out["K 線型態"] == "K 线形态"
    assert out["滑價"] == "滑点"
