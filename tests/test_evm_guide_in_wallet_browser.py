"""錢包內建瀏覽器內不得再列「跳去錢包」清單（2026-09-10 DANNY 朋友 iOS
實測回圈）。

回圈機制：使用者在錢包內建瀏覽器開本站 → 點連接錢包 → 引導面板列出
「跳去錢包 App」的清單 → 點自己那顆 → universal link 在錢包瀏覽器內
只會重新開啟本站 → 面板再現 → 無限循環。

根治：偵測到注入 provider（=已在錢包瀏覽器）或 UA 特徵顯示在錢包
webview 內時，不渲染錢包清單——
  - 有注入：只留「直接連接此瀏覽器的錢包」
  - 無注入（偵測失敗的兜底）：顯示說明＋掃碼 fallback，不出會回圈的清單
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _load_guide_module():
    # 以純文字解析即可（repo 慣例：source-grep 不 import 前端模組）
    return _read("web/js/evm-mobile-guide.js")


class TestIsInsideWalletBrowser:
    def test_exported_and_tokens(self):
        src = _load_guide_module()
        assert "export function isInsideWalletBrowser" in src, "需匯出 isInsideWalletBrowser"
        for token in ("MetaMask", "Bitget", "CoinbaseWallet"):
            assert f"'{token}'" in src, f"UA 特徵清單需含 {token}"

    def test_node_unit_cases(self):
        """node 跑 mjs 的 UA 判定單元（無 node 環境時 skip）。"""
        import shutil
        import subprocess

        if not shutil.which("node"):
            return
        r = subprocess.run(
            ["node", "--test", str(REPO / "tests/js/evm_mobile_guide.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert r.returncode == 0, r.stdout + r.stderr


class TestGuidePanelNoJumpListInsideWalletBrowser:
    def test_links_not_built_when_in_wallet_browser(self):
        src = _read("web/js/evm-auth.js")
        block = re.search(r"function _showOpenInWalletGuidance.*?\n\}\n", src, re.S)
        assert block, "找不到 _showOpenInWalletGuidance"
        body = block.group(0)
        assert "inWalletBrowser = hasInjected || isInsideWalletBrowser()" in body, (
            "需以注入偵測＋UA 特徵雙訊號判定已在錢包瀏覽器"
        )
        assert re.search(r"links = inWalletBrowser\s*\?\s*\[\]\s*:\s*buildWalletBrowserLinks", body), (
            "已在錢包瀏覽器時不得建置「跳去錢包」清單（universal link 回圈根源）"
        )

    def test_direct_row_kept_when_injected(self):
        src = _read("web/js/evm-auth.js")
        block = re.search(r"function _showOpenInWalletGuidance.*?\n\}\n", src, re.S)
        body = block.group(0)
        assert "const directRow = hasInjected" in body, "有注入時直接連接列必須保留"

    def test_wc_fallback_hidden_when_injected(self):
        """有注入時掃碼 fallback 一併隱藏——直接連接就在眼前，掃碼只會混亂。"""
        src = _read("web/js/evm-auth.js")
        block = re.search(r"function _showOpenInWalletGuidance.*?\n\}\n", src, re.S)
        body = block.group(0)
        assert re.search(r"evm-guide-wc-fallback[^>]*\$\{hasInjected \? 'hidden' : ''\}", body) or re.search(
            r"hasInjected \? 'hidden' : ''", body
        ), "有注入時 WC 掃碼列應隱藏"

    def test_no_injection_note_i18n_keys(self):
        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = _read(f"web/js/i18n/{lang}.json")
            assert "inWalletNoInjection" in data, f"{lang}.json 缺 evmAuth.inWalletNoInjection"
        src = _read("web/js/evm-auth.js")
        assert "evmAuth.inWalletNoInjection" in src, "面板需使用 inWalletNoInjection 說明"
