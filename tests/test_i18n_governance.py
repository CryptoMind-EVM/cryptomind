"""
後端 i18n governance tests。

防止 i18n 回歸：
1. core/i18n/*.json 每個 namespace 必須 4 語齊全（zh-TW/zh-CN/en/ru）
2. 所有 namespace 的 key 集合必須一致（不能某語言缺某 key）
3. 值不能是空字串
4. t() 基本 API 行為：精確匹配 / fallback / 變數插值 / unknown key 不爆
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    list_namespaces,
    reload_for_tests,
    t,
)

I18N_DIR = Path(__file__).parent.parent / "core" / "i18n"


@pytest.fixture(autouse=True)
def _reload_i18n():
    """每個 test 前重新載入，避免跨 test 污染。"""
    reload_for_tests()


# ============================================================================
# 1. JSON 檔結構 governance
# ============================================================================


@pytest.fixture(scope="module")
def all_namespaces_data():
    """讀取所有 namespace JSON（不經 t()，直接驗結構）。"""
    data = {}
    for json_file in I18N_DIR.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            data[json_file.stem] = json.load(f)
    return data


def test_i18n_directory_has_at_least_one_namespace(all_namespaces_data):
    """core/i18n/ 必須有 JSON 檔。"""
    assert len(all_namespaces_data) > 0, "core/i18n/ 沒有任何 JSON namespace 檔"


@pytest.mark.parametrize("namespace", ["errors", "ui_messages", "llm_sections"])
def test_expected_namespaces_exist(all_namespaces_data, namespace):
    """核心 namespace 必須存在。新增 namespace 請加進這裡。"""
    assert namespace in all_namespaces_data, (
        f"缺少必要的 i18n namespace: {namespace}.json"
    )


def test_all_namespaces_have_4_languages(all_namespaces_data):
    """每個 namespace 必須涵蓋 4 個支援語言。

    注意：``_todo_native_review`` / ``_comment`` 等底線開頭的 key 是 metadata，
    不算語言欄位，會被忽略。
    """
    missing = {}
    for ns_name, ns_data in all_namespaces_data.items():
        absent = [lang for lang in SUPPORTED_LANGUAGES if lang not in ns_data]
        if absent:
            missing[ns_name] = absent
    assert not missing, (
        f"以下 namespace 缺少語言覆蓋（每個必須有全部 {SUPPORTED_LANGUAGES}）：\n"
        + "\n".join(f"  {k}: 缺 {v}" for k, v in missing.items())
    )


def test_all_namespaces_have_consistent_keys(all_namespaces_data):
    """所有語言的 key 集合必須一致。

    例如 zh-TW 有 ``errors.analysis.timeout``，en/zh-CN/ru 也必須有。
    避免某語言悄悄缺 key 導致 fallback 到 zh-TW（用戶看到非預期語言）。
    """
    inconsistent = {}
    for ns_name, ns_data in all_namespaces_data.items():
        # 取每個語言的 key 集合（排除 _ 底線開頭的 metadata）
        lang_keys = {}
        for lang, kv in ns_data.items():
            if lang in SUPPORTED_LANGUAGES and isinstance(kv, dict):
                lang_keys[lang] = {
                    k for k in kv.keys() if not k.startswith("_")
                }
        if not lang_keys:
            continue
        # 以 zh-TW 為基準（主市場）
        baseline = lang_keys.get(DEFAULT_LANGUAGE, set())
        for lang, keys in lang_keys.items():
            missing_vs_baseline = baseline - keys
            extra_vs_baseline = keys - baseline
            if missing_vs_baseline or extra_vs_baseline:
                inconsistent[f"{ns_name}.{lang}"] = {
                    "missing": sorted(missing_vs_baseline),
                    "extra": sorted(extra_vs_baseline),
                }
    assert not inconsistent, (
        "namespace 各語言 key 不一致（應以 zh-TW 為基準對齊）：\n"
        + json.dumps(inconsistent, ensure_ascii=False, indent=2)
    )


def test_no_empty_translation_values(all_namespaces_data):
    """翻譯值不能是空字串（會被 fallback 掩蓋問題）。"""
    empties = []
    for ns_name, ns_data in all_namespaces_data.items():
        for lang, kv in ns_data.items():
            if lang not in SUPPORTED_LANGUAGES or not isinstance(kv, dict):
                continue
            for key, value in kv.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, str) and not value.strip():
                    empties.append(f"{ns_name}.{lang}.{key}")
    assert not empties, f"以下位置是空字串：{empties}"


# ============================================================================
# 2. t() API 行為
# ============================================================================


@pytest.mark.parametrize("lang", list(SUPPORTED_LANGUAGES))
def test_t_returns_translation_for_all_languages(lang):
    """每個語言都該拿到非空翻譯（不是 key 本身）。"""
    msg = t("errors.analysis.cannot_process", lang)
    assert isinstance(msg, str)
    assert msg and msg != "errors.analysis.cannot_process", (
        f"{lang} 拿到空翻譯或 key 本身"
    )


def test_t_variable_interpolation():
    """{var} 插值要能正確替換。"""
    msg = t("errors.analysis.timeout", "zh-TW", seconds=99)
    assert "99" in msg
    assert "逾時" in msg or "超過" in msg


def test_t_variable_interpolation_english():
    msg = t("errors.analysis.timeout", "en", seconds=30)
    assert "30" in msg
    assert "timeout" in msg.lower() or "exceeded" in msg.lower()


def test_t_falls_back_to_zh_tw_for_unknown_language():
    """未知語言 → fallback 到 zh-TW（主市場）。"""
    msg_klingon = t("errors.analysis.cannot_process", "klingon")
    msg_zh_tw = t("errors.analysis.cannot_process", "zh-TW")
    assert msg_klingon == msg_zh_tw


def test_t_falls_back_to_zh_tw_for_zh_variant():
    """未知中文變體（zh-HK 等）→ zh-TW。"""
    msg_hk = t("errors.analysis.cannot_process", "zh-HK")
    msg_tw = t("errors.analysis.cannot_process", "zh-TW")
    assert msg_hk == msg_tw


def test_t_falls_back_to_zh_tw_for_none():
    msg_none = t("errors.analysis.cannot_process", None)
    msg_tw = t("errors.analysis.cannot_process", "zh-TW")
    assert msg_none == msg_tw


def test_t_unknown_key_returns_key_itself():
    """未知 key 不爆，回 key 本身（避免 i18n 失敗擋住主流程）。"""
    unknown = t("this.key.does.not.exist", "zh-TW")
    assert unknown == "this.key.does.not.exist"


def test_t_handles_missing_variable_gracefully():
    """插值時若 var 沒給，保留原 placeholder（不爆）。"""
    msg = t("errors.analysis.timeout", "zh-TW")  # 沒給 seconds
    # 應保留 {seconds} placeholder
    assert "{seconds}" in msg


# ============================================================================
# 3. 簡繁正確性（ru 不檢查，因機翻）
# ============================================================================


def test_zh_cn_errors_are_simplified():
    """errors namespace 的 zh-CN 必須是簡中（含簡體特徵字）。"""
    msg = t("errors.analysis.cannot_process", "zh-CN")
    # 「抱歉」簡繁同形；改檢查「请」（簡）vs「請」（繁）
    assert "请" in msg or "暂时" in msg, f"zh-CN errors 不是簡中: {msg!r}"
    assert "請" not in msg, f"zh-CN errors 含繁體『請』: {msg!r}"


def test_zh_tw_errors_are_traditional():
    msg = t("errors.analysis.cannot_process", "zh-TW")
    assert "請" in msg or "處理" in msg
    assert "请" not in msg


# ============================================================================
# 4. namespace 列舉 sanity check
# ============================================================================


def test_list_namespaces_returns_loaded_namespaces():
    namespaces = list_namespaces()
    assert isinstance(namespaces, list)
    assert "errors" in namespaces
    assert "ui_messages" in namespaces
