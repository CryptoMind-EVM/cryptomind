"""validate-key 端點參數守護 — 確保 reasoning 模型在 NVIDIA 端點不會間歇超時。

線上 #特斯拉案例（DANNY Zeabur 回報）：GLM-5.2 在 NVIDIA 端點回應極不穩定
（實測 0.8s~27s），原 validate-key 後端 timeout 25s 常被吃光，加上 Zeabur
跨洋部署延遲，導致 TEST 按鈕間歇性失敗。

修法（api/routers/system.py）：
  1. 測試 prompt 改極短閉合式（'Reply "ok"'）— reasoning 模型不展開長思考鏈
  2. max_tokens 1024→256 — 驗證只需短回覆，降低 reasoning 耗時
  3. 後端 timeout 25s→45s — 容納最壞情況 + 跨洋延遲
  4. init_chat_model 加 request_timeout=60 — 避免 SDK 內部先斷
  5. 前端 timeout 30s→50s（web/js/llmSettings.js）— 需 > 後端

本檔用原始碼檢查守護這些參數不被誤改回舊值。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PY = ROOT / "api" / "routers" / "system.py"
LLM_SETTINGS_JS = ROOT / "web" / "js" / "llmSettings.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================================
# 測試 prompt：必須是極短閉合式（reasoning 模型快速收斂）
# ============================================================================


def test_validate_key_prompt_is_short_and_closed():
    """測試 prompt 必須極短且閉合式（要求單字/短詞回覆）。

    開放性 prompt（如 'respond briefly'）會讓 reasoning 模型（GLM-5.2/nemotron）
    展開完整思考鏈，浪費 18-27 秒；閉合式 prompt（'Reply "ok"'）能壓到 0.5-2 秒。
    """
    src = _read(SYSTEM_PY)
    # 抓 test_prompt 賦值
    match = re.search(r'test_prompt\s*=\s*["\'](.+?)["\']', src)
    assert match, "找不到 test_prompt 定義"
    prompt = match.group(1)
    # 必須短（< 100 字元）且要求簡短回覆（ok/yes/單字）
    assert len(prompt) < 100, (
        f"測試 prompt 太長({len(prompt)}字元)，reasoning 模型會展開思考鏈浪費時間: {prompt!r}"
    )
    assert re.search(r"\bok\b|single word|one word|briefly", prompt, re.I), (
        f"測試 prompt 應要求極簡短回覆（如 'Reply ok'）: {prompt!r}"
    )


# ============================================================================
# max_tokens：驗證用，不需太大
# ============================================================================


def test_validate_key_max_tokens_not_too_large():
    """max_tokens 不應超過 512 — 驗證只需短回覆，太大會讓 reasoning 模型多思考。

    原 1024 偏大；256 對閉合式 prompt 足夠（reasoning + content 都裝得下）。
    """
    src = _read(SYSTEM_PY)
    match = re.search(r"max_tokens\s*=\s*(\d+)", src)
    assert match, "找不到 max_tokens 設定"
    val = int(match.group(1))
    assert val <= 512, (
        f"validate-key max_tokens={val} 太大，reasoning 模型會浪費時間思考"
        "（驗證只需短回覆，建議 ≤256）"
    )
    assert val >= 64, (
        f"validate-key max_tokens={val} 太小，reasoning 模型思考完就沒額度輸出 content"
        "（會 finish_reason=length，表現為「無回應」）"
    )


# ============================================================================
# 後端 timeout：必須 ≥ 40s 容納 reasoning 模型 + 跨洋延遲
# ============================================================================


def test_validate_key_backend_timeout_sufficient():
    """後端 asyncio.wait_for timeout 必須 ≥ 40s。

    GLM-5.2 在 NVIDIA 端點實測 0.8s~27s，加上 Zeabur 跨洋延遲，原 25s 會間歇超時。
    """
    src = _read(SYSTEM_PY)
    # validate-key 函式體（從 validate_key 到下一個 endpoint 裝飾器）
    fn_start = src.find("async def validate_key(")
    fn_end = src.find("@router.post", fn_start + 1)
    if fn_end < 0:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    # 抓 invoke 那段 wait_for 的 timeout（在 test_prompt 之後、含 llm.invoke）
    invoke_section = fn_body.split("llm.invoke")[1][:300] if "llm.invoke" in fn_body else ""
    match = re.search(r"timeout\s*=\s*(\d+\.?\d*)", invoke_section)
    assert match, "找不到 validate-key 的 asyncio.wait_for timeout（llm.invoke 區段）"
    val = float(match.group(1))
    assert val >= 40, (
        f"validate-key 後端 timeout={val}s 太短，GLM-5.2 在 NVIDIA 端點實測可達 27s，"
        f"加上跨洋延遲會間歇超時（線上 #特斯拉案例）"
    )


def test_validate_key_request_timeout_set():
    """init_chat_model 必須設 request_timeout，避免 SDK 內部預設 timeout 先斷。"""
    src = _read(SYSTEM_PY)
    assert "request_timeout" in src, (
        "init_chat_model 沒設 request_timeout，SDK 預設可能比外層 asyncio.wait_for 短，"
        "會先斷導致驗證失敗"
    )


# ============================================================================
# 前端 timeout：必須 > 後端 timeout
# ============================================================================


def test_validate_key_frontend_timeout_exceeds_backend():
    """前端 timeout 必須 > 後端（45s），否則前端先 abort 誤報失敗。"""
    backend_src = _read(SYSTEM_PY)
    fn_start = backend_src.find("async def validate_key(")
    fn_end = backend_src.find("@router.post", fn_start + 1)
    if fn_end < 0:
        fn_end = len(backend_src)
    fn_body = backend_src[fn_start:fn_end]
    invoke_section = fn_body.split("llm.invoke")[1][:300] if "llm.invoke" in fn_body else ""
    be_match = re.search(r"timeout\s*=\s*(\d+\.?\d*)", invoke_section)
    backend_to = float(be_match.group(1))

    frontend_src = _read(LLM_SETTINGS_JS)
    # 抓 validate-key 呼叫的 timeout（毫秒）。
    # 錨定實際呼叫的 URL 字串——檔案其他處（如 line ~504 註解）也可能出現
    # "validate-key"，純關鍵字 split 會抓錯段落（2026-08-30 修）。
    validate_section = frontend_src.split("/api/settings/validate-key")[1][:600]
    fe_match = re.search(r"timeout\s*:\s*(\d+)", validate_section)
    assert fe_match, "前端 validate-key 呼叫找不到 timeout 設定"
    frontend_to = int(fe_match.group(1)) / 1000  # ms → s

    assert frontend_to > backend_to, (
        f"前端 timeout({frontend_to}s) 必須 > 後端({backend_to}s)，"
        f"否則前端先 abort 誤報「連線測試失敗」"
    )
