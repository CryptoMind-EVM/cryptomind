"""api-client 截斷回應防禦（2026-09-09）。

生產事故：Service Worker 更新（skipWaiting+clientsClaim、precache 全量重抓）
在頁面運行中接管，窗口內的 API 回應被截斷——瀏覽器拿到「200 +
application/json + 空 body」，response.json() 炸出
「Unexpected end of JSON input」並以原始英文進 toast。
時間線鐵證（07:33 UTC，EVM_2a48e2）：sw.js 下載 → workbox precache
風暴 → 25 秒後 link-token 第一次點擊截斷、第二次正常。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
API_CLIENT = REPO / "web" / "js" / "api-client.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _js() -> str:
    return API_CLIENT.read_text(encoding="utf-8")


def _code_only() -> str:
    """剝掉註解——ratchet 測試只看可執行碼（fallback 一律英文）。"""
    src = re.sub(r"/\*.*?\*/", "", _js(), flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


class TestTruncationGuard:
    def test_truncation_signature_detected_before_parse(self):
        """ok + json content-type + 空 body 必須先進防禦，不得直接 parse。"""
        js = _js()
        assert "_handleTruncatedResponse" in js, "找不到截斷防禦 handler"
        assert "response.json()" not in _code_only(), (
            "成功路徑不得用 response.json()——空 body 會炸出未處理的 "
            "SyntaxError（本次事故的 toast 來源）"
        )
        assert re.search(r"if \(!text\)\s*\{\s*return await _handleTruncatedResponse", js), (
            "text 為空必須導入 _handleTruncatedResponse"
        )

    def test_partial_truncated_body_routes_to_guard(self):
        """SW 接管窗口也可能把 body 切在半中間——非空壞 JSON 一樣要進
        _handleTruncatedResponse，不得讓 SyntaxError 以原始英文見使用者。"""
        js = _js()
        assert re.search(
            r"try\s*\{\s*return JSON\.parse\(text\);?\s*\}\s*"
            r"catch\s*\([^)]*\)\s*\{\s*return await _handleTruncatedResponse",
            js,
        ), "JSON.parse 失敗必須導入 _handleTruncatedResponse（部分截斷 body）"

    def test_retry_safe_post_allowlist_covers_link_tokens(self):
        """link-token 類 POST 再產一張無副作用——允許自動重試一次。"""
        js = _js()
        for url in ("/api/line/link-token", "/api/telegram/link-token"):
            assert url in js, f"retry-safe 白名單缺 {url}"
        assert "_truncationRetried" in js, "重試必須單次（防無限迴圈）"

    def test_telemetry_marker_for_error_boundary(self):
        """截斷發生當下要留 console.error——error-boundary 會批量上報，
        補足本次事故『server 端完全看不到』的遙測缺口。"""
        assert "[api-client] truncated response" in _js()

    def test_friendly_error_is_localized_not_raw_syntax_error(self):
        """不可再讓「Unexpected end of JSON input」這類原始訊息見使用者。"""
        code = _code_only()
        assert "networkUnstable" in code, "截斷錯誤要走 i18n key"
        assert "Unexpected end of JSON input" not in code

    def test_executable_code_english_fallback_only(self):
        """棘輪慣例：可執行碼不出现中文，翻譯交給 i18n 檔。"""
        offenders = [
            line.strip()
            for line in _code_only().splitlines()
            if re.search(r"[一-鿿]", line)
        ]
        assert not offenders, f"api-client 可執行碼不該有中文：{offenders[:3]}"


def test_network_unstable_key_in_every_locale():
    for lang in LOCALES:
        data = json.loads(
            (REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")
        )
        assert data.get("common", {}).get("networkUnstable"), (
            f"{lang}.json 缺 common.networkUnstable"
        )


def test_truncated_error_never_enters_outer_retry_loop():
    """截斷錯誤不得進外層 attempt 迴圈（code review 2026-09-10）：
    GET（retries=3）與 _handleTruncatedResponse 的單次重試相乘，最壞
    20 次請求／約 30 秒卡死＋洗版遙測＋可能撞 rate limit——外層 catch
    必須直接重丟。"""
    catch_block = re.search(r"catch \(err\) \{(.*?)lastError = err", _js(), re.S)
    assert catch_block, "找不到 request() 的 catch 區塊"
    assert re.search(
        r"if \(err && err\.truncated\) throw err;", catch_block.group(1)
    ), "catch 開頭必須直接重丟截斷錯誤（單次重試已在 handler 內完成）"
