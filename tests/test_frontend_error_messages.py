"""使用者不再看到原始英文錯誤字串（API 層與錢包登入，2026-10-06）。

很多 catch 把 e.message 直接貼進 toast／alert／innerHTML：'Failed to fetch'、'Status 502: Bad Gateway'、
'pin_limit_reached'、錢包的 'User denied message signature'。顯示層 helper 在 web/js/error-message.js；
行為在 tests/js/error_message.mjs（helper 本身）與 tests/js/error_message_callers.mjs（各呼叫端），
這裡包成 pytest 並守住接線：不能退回「直接貼 e.message」，也不能動到靠 .message 比對的位置。
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
LANGS = ("zh-TW", "zh-CN", "en", "ru")


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _code(rel: str) -> str:
    """去掉整行註解與行尾 `// ...` 註解——ratchet 只看可執行碼（註解裡常會引用舊寫法當說明）。"""
    out = []
    for line in _read(rel).splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        out.append(re.sub(r"\s//\s.*$", "", line))
    return "\n".join(out)


def _resolve(d, key):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


@pytest.mark.parametrize("script", ["error_message.mjs", "error_message_callers.mjs"])
def test_node_behaviour(script):
    proc = subprocess.run(
        ["node", f"tests/js/{script}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


# ── 不能再直接貼原始錯誤訊息 ──
# 檔案 → 不得出現的寫法。forum-app 的付款／打賞段落（paymentError?.message、'Tip failed' + e.message、
# 付款後發文失敗視窗）刻意不碰，所以只守「toast 的 e.message／err.message」這幾種。
RAW_TOAST = re.compile(r"(showToast|alert)\(\s*\(?\s*(e|err|error)\.message\b")
RAW_SUFFIX = re.compile(r"(['\"`])\s*\+\s*\(?\s*(e|err|error)\.message\b")
RAW_CALLERS = [
    "web/js/friends.js",
    "web/js/forum-app.js",
    "web/forum/js/profile-page.js",
    "web/js/google-auth.js",
    "web/js/trustScoreManager.js",
    "web/js/walletMonitorTab.js",
    "web/js/skill-manager.js",
    "web/js/components/tab-journal.js",
    "web/js/chat-analysis.js",
    "web/js/toolSettings.js",
    "web/js/legal.js",
]


@pytest.mark.parametrize("rel", RAW_CALLERS)
def test_no_raw_error_message_in_toasts(rel):
    src = _code(rel)
    offenders = [m.group(0) for m in RAW_TOAST.finditer(src)]
    if rel == "web/js/forum-app.js":
        # 打賞段落：'Tip failed' + e.message 在 handleTip 內，付款相關，不在這次範圍
        offenders += [
            m.group(0)
            for m in RAW_SUFFIX.finditer(src)
            if "tipFailed" not in src[max(0, m.start() - 200) : m.start()]
        ]
    else:
        offenders += [m.group(0) for m in RAW_SUFFIX.finditer(src)]
    assert not offenders, f"{rel} 還在把原始錯誤訊息貼給使用者：{offenders}"


def test_legal_and_admin_do_not_print_raw_messages():
    legal = _code("web/js/legal.js")
    assert "Failed to load:" not in legal and "Content not found." not in legal
    admin = _code("web/js/admin.js")
    assert "Failed to load users:" not in admin and "Failed to load user:" not in admin
    assert "admin.loadUsersFailed" in admin and "admin.loadUserFailed" in admin
    journal = _code("web/js/components/tab-journal.js")
    assert "'Save failed: '" not in journal


def test_raw_fetch_callers_use_error_from_response():
    """繞過 api-client 的 raw fetch：detail 若是 422 陣列會變 '[object Object]'，HTML 502 被 res.json() 炸成 SyntaxError。"""
    wallet = _code("web/js/walletMonitorTab.js")
    # 會顯示訊息的三處（新增／儲存設定／移除）；showDetail 失敗只是退回列表，不顯示
    assert wallet.count("throw await errorFromResponse(resp)") == 3
    assert "throw new Error(err.detail" not in wallet
    assert "resp.clone().json()" in wallet, (
        "_isUpgradeBlock 讀完 body，後面 errorFromResponse 才讀得到 detail 要靠 clone"
    )
    skill = _code("web/js/skill-manager.js")
    assert (
        "resp.json().catch" not in skill and "throw new Error(err.detail" not in skill
    )
    trust = _code("web/js/trustScoreManager.js")
    assert "Signature verification failed" not in trust and "nonce HTTP" not in trust


# ── 錢包取消的判斷只有一份 ──
def test_evm_auth_uses_shared_rejection_detection():
    src = _read("web/js/evm-auth.js")
    assert "e.code === 4001 ||" not in src, (
        "取消判斷改走 _isUserRejection（error-message.js），不要各處自己寫"
    )
    assert src.count("if (_isUserRejection(e)) {") >= 3, (
        "login／bind／WC 續登三個 catch 都要用同一個判斷"
    )
    assert "userFacingMessage(e, { fallbackKey: 'evmAuth.bindFailed'" in src
    # 錢包原文只在「看得懂」時附在括號裡
    assert "presentableMessage(e)" in src


def test_trust_score_manager_treats_rejection_as_cancel():
    src = _read("web/js/trustScoreManager.js")
    assert (
        "isUserRejection(e)" in src and "trust.bindCancelled" not in src
    )  # key 經 _t('bindCancelled')
    assert "_t('bindCancelled')" in src


# ── 不能破壞：AppAPI 的 .message 內容與 import 狀態 ──
def test_api_client_message_contract_untouched():
    src = _read("web/js/api-client.js")
    code = "\n".join(
        line
        for line in src.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    # filter.js 比對 'Failed to fetch'、usdc-pay.js 靠 status===0 重試、premium.js 靠 .message：這些字面不能變
    assert "'Request timeout (' + timeout + 'ms)'" in code
    assert "lastError.status = 0;" in code
    assert "lastError.timeout = true;" in code, (
        "顯示層靠這個旗標分辨逾時（.message 維持英文）"
    )
    assert "'Status ' + response.status + ': ' + response.statusText" in code
    # tests/js 的 node 閘門（auth_init_once／legacy_ton_notice）把這支原始碼直接包成 data: URL，不能有相對 import
    assert not re.search(r"^\s*import\s", code, re.M), (
        "api-client.js 不得 import（node 閘門以 data: URL 載入）"
    )


def test_untouchable_message_consumers_still_match_on_raw_message():
    """靠 .message 做 regex／比對的位置——顯示層不能動到它們（PR 沒碰這些檔案，這裡鎖住比對依據還在）。"""
    assert "Failed to fetch" in _read("web/js/filter.js")
    assert "e.status === 429 && /Daily comment limit/.test(e.message" in _read(
        "web/js/forum-app.js"
    )
    assert "err?.message === 'pin_limit_reached'" in _read("web/js/social-pins.js")


def test_error_message_is_loaded_for_classic_scripts():
    """skill-manager／profile-page 是 classic script，不能 import——靠 window.ErrorMessage；入口要載入它。"""
    assert "window.ErrorMessage" in _read("web/js/error-message.js")
    assert "./error-message.js" in _read("web/js/main.js")
    assert "./error-message.js" in _read("web/js/friends.js"), (
        "論壇個人頁（profile-page.js）靠 friends.js 帶進 window.ErrorMessage"
    )
    for rel in (
        "web/js/skill-manager.js",
        "web/forum/js/profile-page.js",
        "web/js/memory-manager.js",
    ):
        assert not re.search(r"^\s*import\s", _read(rel), re.M), (
            f"{rel} 是 classic script，不能有 import"
        )
    assert "window.ErrorMessage" in _read("web/js/skill-manager.js")
    assert "window.ErrorMessage" in _read("web/forum/js/profile-page.js")


def test_error_message_gets_its_own_chunk_above_forum_app_core():
    """forum-app（forum-app-core）、friends（shared-core）、main 都 import error-message。沒有自己的
    高優先權 chunk 的話，forum-app-core 會連依賴把它吃掉，shared-core／main 反過來 import
    forum-app-core——SPA 開頁就把整個論壇模組載進來（實測 vite build 的 chunk 圖；同 usdc-pay 的問題）。"""
    cfg = _read("vite.config.js")
    group = re.search(r"\{\s*name: 'error-message',\s*priority: (\d+),[^}]*\}", cfg)
    forum = re.search(r"\{ name: 'forum-app-core', priority: (\d+),", cfg)
    assert group and forum
    assert int(group.group(1)) > int(forum.group(1))
    for module in ("error-message", "rejection-filter", "stale-chunk-recovery"):
        assert module in group.group(0), (
            f"{module} 是 error-message 的依賴，要在同一個高優先權 chunk"
        )


def test_classic_script_cache_busting_bumped():
    assert "profile-page.js?v=5" in _read("web/forum/profile.html")
    assert "skill-manager.js?v=6" in _read("web/index.html")


# ── 論壇文章頁：後端 GET 文章會清留言通知，側欄紅點要自己叫重抓 ──
def test_forum_post_detail_refreshes_nav_badges():
    src = _read("web/js/forum-app.js")
    start = src.index("async loadPostDetail(id) {")
    end = src.index("updateAuthorActions(post) {", start)
    body = src[start:end]
    assert "window.NavBadges?.schedule()" in body
    # 只在成功路徑：放在 catch 之前（失敗時文章沒載入，後端也不會清通知）
    assert body.index("window.NavBadges?.schedule()") < body.rindex("} catch (e) {")


# ── i18n：呼叫端用到的 fallbackKey 與新增的 key 四語都在 ──
def test_fallback_keys_exist_in_all_locales():
    locales = {lang: json.loads(_read(f"web/js/i18n/{lang}.json")) for lang in LANGS}
    keys = set()
    for rel in RAW_CALLERS + ["web/js/evm-auth.js", "web/js/admin.js"]:
        keys.update(re.findall(r"fallbackKey:\s*'([A-Za-z0-9_.]+)'", _read(rel)))
    keys.update(
        re.findall(
            r"friendActionError\([^,]+,\s*'([A-Za-z0-9_.]+)'\)",
            _read("web/forum/js/profile-page.js"),
        )
    )
    assert len(keys) >= 20, f"掃描規模異常：{sorted(keys)}"
    missing = [
        f"{key}（缺 {','.join(lang for lang in LANGS if _resolve(locales[lang], key) is None)}）"
        for key in sorted(keys)
        if any(_resolve(locales[lang], key) is None for lang in LANGS)
    ]
    assert not missing, "\n".join(missing)


def test_new_error_keys_are_translated_not_copied():
    """en／ru 不能是 zh-TW 的複本（漏翻的典型症狀）。"""
    zh = json.loads(_read("web/js/i18n/zh-TW.json"))
    for lang in ("en", "ru"):
        other = json.loads(_read(f"web/js/i18n/{lang}.json"))
        for key in (
            "error.cancelled",
            "error.timeout",
            "error.badRequest",
            "error.requestFailed",
            "error.codes.pro_required",
            "error.details.alreadyFriends",
            "trust.bindSignatureFailed",
            "journal.saveFailed",
            "admin.loadUsersFailed",
        ):
            assert _resolve(other, key) != _resolve(zh, key), (
                f"{lang} 的 {key} 還是中文"
            )
            assert not re.search(r"[一-鿿]", _resolve(other, key)), (
                f"{lang} 的 {key} 含中文"
            )
