"""scripts/i18n/translate.py：zh-TW 單一來源 → en／ru 翻譯管線。

2026-09-25 DANNY 定案：zh-TW 是唯一手改的語系，en／ru 只翻新增或 zh-TW 改過的
key（逐 key 指紋），LLM 起草、PR diff 審。這裡不打網路：LLM 一律用假的 httpx.post。
最後一條是守衛：repo 上的 lock 必須與 catalog 同步。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "i18n_translate", ROOT / "scripts" / "i18n" / "translate.py"
)
tr = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = tr  # dataclass 解析註解時要找得到自己的模組
_SPEC.loader.exec_module(tr)

API_KEY = "fake-i18n-test-key-123"
ENV = {
    "I18N_LLM_BASE_URL": "https://llm.example/v1",
    "I18N_LLM_API_KEY": API_KEY,
    "I18N_LLM_MODEL": "fake-model",
}

ZH_TW_WEB = {
    "common": {"save": "儲存", "hello": "你好 {{name}}", "open": "打開 <b>錢包</b>"},
    "nav": {"wallet": "錢包"},
}
EN_WEB = {  # 順序刻意跟 zh-TW 不同
    "nav": {"wallet": "Wallet"},
    "common": {"hello": "Hello {{name}}", "save": "Save", "open": "Open <b>wallet</b>"},
}
RU_WEB = {
    "common": {
        "save": "Сохранить",
        "hello": "Привет, {{name}}",
        "open": "Открыть <b>кошелёк</b>",
    },
    "nav": {"wallet": "Кошелёк"},
}
ZH_CN_WEB = {"common": {"save": "保存"}}


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    web = tmp_path / "web" / "js" / "i18n"
    for lang, data in (
        ("zh-TW", ZH_TW_WEB),
        ("en", EN_WEB),
        ("ru", RU_WEB),
        ("zh-CN", ZH_CN_WEB),
    ):
        _dump(web / f"{lang}.json", data)
    core = tmp_path / "core" / "i18n"
    _dump(
        core / "errors.json",
        {
            "zh-TW": {"a.timeout": "逾時 {seconds} 秒"},
            "zh-CN": {"a.timeout": "超时 {seconds} 秒"},
            "en": {"a.timeout": "Timed out after {seconds}s"},
            "ru": {"_todo_native_review": True, "a.timeout": "Тайм-аут {seconds} с"},
        },
    )
    _dump(
        core / "llm_sections.json",
        {
            "_comment": "LLM prompt 區段",
            "zh-TW": {"nudge.lang": "請用繁體中文回答。"},
            "zh-CN": {"nudge.lang": "请用简体中文回答。"},
            "en": {"nudge.lang": "Answer in English."},
            "ru": {
                "_todo_native_review": True,
                "nudge.lang": "Отвечайте на русском языке.",
            },
        },
    )
    _dump(
        core / "ui_messages.json",
        {
            "zh-TW": {"p.x": "分析中"},
            "zh-CN": {"p.x": "分析中"},
            "en": {"p.x": "Analyzing"},
            "ru": {"p.x": "Анализ"},
        },
    )
    _dump(
        tmp_path / "scripts" / "i18n" / "glossary.json",
        {
            "_comment": "test",
            "錢包": {"en": "wallet", "ru": "кошелёк"},
        },
    )
    return tmp_path


def run(root: Path, *argv: str, env: dict | None = None) -> int:
    return tr.main(list(argv), root=root, env={} if env is None else env)


def _set_zh_tw(root: Path, section: str, key: str, value: str) -> None:
    path = root / "web" / "js" / "i18n" / "zh-TW.json"
    data = _load(path)
    data[section][key] = value
    _dump(path, data)


def _keys_in_order(tree: dict, prefix: str = "") -> list[str]:
    out = []
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        out.extend(_keys_in_order(value, path) if isinstance(value, dict) else [path])
    return out


class _Resp:
    def __init__(self, payload=None, status: int = 200, text: str = ""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self._payload


def _ok(content: str, finish: str = "stop") -> _Resp:
    return _Resp(
        {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    )


@pytest.fixture
def fake_llm(monkeypatch):
    """回傳 (calls, set_answer)。set_answer(fn)：fn(lang, key, item) → 譯文；None = 不輸出這個 key。"""
    calls, state = [], {"fn": None, "raw": None}

    def fake_post(url, **kwargs):  # httpx.post(url, json=..., headers=..., timeout=...)
        payload = kwargs["json"]
        calls.append({"url": url, "payload": payload, "headers": kwargs["headers"]})
        user = json.loads(payload["messages"][1]["content"])
        if state["raw"]:
            return state["raw"](user)
        lang = "en" if user["target_language"] == "English" else "ru"
        out = {}
        for key, item in user["items"].items():
            value = state["fn"](lang, key, item)
            if value is not None:
                out[key] = value
        return _ok(json.dumps(out, ensure_ascii=False))

    monkeypatch.setattr(tr.httpx, "post", fake_post)
    monkeypatch.setattr(tr, "RETRY_DELAY", 0)

    def set_answer(fn=None, raw=None):
        state["fn"], state["raw"] = fn, raw

    return calls, set_answer


@pytest.fixture
def no_http(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("不該呼叫 LLM")

    monkeypatch.setattr(tr.httpx, "post", boom)


# ─── 指紋與 staleness ───────────────────────────────────────────────────────


def test_fingerprint_is_sha256_prefix():
    assert tr.fingerprint("儲存") == hashlib.sha256("儲存".encode()).hexdigest()[:16]


def test_init_lock_makes_check_pass(repo):
    assert run(repo, "check") == 1  # 沒有 lock：全部 stale
    assert run(repo, "init-lock") == 0
    assert run(repo, "check") == 0
    raw = (repo / tr.LOCK_PATH).read_text(encoding="utf-8")
    lock = json.loads(raw)
    assert raw.endswith("}\n")
    assert list(lock) == sorted(lock) and "_comment" in lock
    assert list(lock["en"]["web"]) == sorted(lock["en"]["web"])
    assert lock["en"]["web"]["common.save"] == tr.fingerprint("儲存")
    assert "_todo_native_review" not in lock["ru"]["core/errors"]


def test_init_lock_refuses_to_overwrite_without_force(repo):
    assert run(repo, "init-lock") == 0
    assert run(repo, "init-lock") == 2
    assert run(repo, "init-lock", "--force") == 0


def test_changing_zh_tw_marks_key_stale_for_en_and_ru(repo):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    ws = tr.Workspace(repo)
    for lang in ("en", "ru"):
        d = ws.diff(lang, "web")
        assert d.stale == ["common.save"] and not d.missing and not d.extra
    assert run(repo, "check") == 1


def test_new_zh_tw_key_is_missing_and_extra_target_key_is_extra(repo):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "newKey", "新功能")
    en = _load(repo / "web/js/i18n/en.json")
    en["common"]["legacy"] = "Old"
    _dump(repo / "web/js/i18n/en.json", en)
    ws = tr.Workspace(repo)
    assert ws.diff("en", "web").missing == ["common.newKey"]
    assert ws.diff("ru", "web").missing == ["common.newKey"]
    assert ws.diff("en", "web").extra == ["common.legacy"]
    # metadata（_todo_native_review）不算 extra
    assert ws.diff("ru", "core/errors").extra == []


# ─── accept ─────────────────────────────────────────────────────────────────


def test_accept_marks_only_given_lang_and_key(repo):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    assert run(repo, "accept", "--lang", "en", "--keys", "web:common.save") == 0
    ws = tr.Workspace(repo)
    assert ws.diff("en", "web").stale == []
    assert ws.diff("ru", "web").stale == ["common.save"]
    # 不帶 catalog 的 key，唯一時也可以
    assert run(repo, "accept", "--lang", "ru", "--keys", "common.save") == 0
    assert run(repo, "check") == 0


def test_accept_all_stale(repo):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    _set_zh_tw(repo, "nav", "wallet", "我的錢包")
    assert run(repo, "accept", "--all-stale") == 0
    assert run(repo, "check") == 0


def test_accept_refuses_missing_unknown_and_invalid_without_writing(repo):
    run(repo, "init-lock")
    before = (repo / tr.LOCK_PATH).read_text(encoding="utf-8")
    _set_zh_tw(repo, "common", "newKey", "新功能")
    assert run(repo, "accept", "--lang", "en", "--keys", "common.newKey") == 1
    assert run(repo, "accept", "--lang", "en", "--keys", "common.nope") == 1
    # 譯文掉了 placeholder：不能標成已審
    _set_zh_tw(repo, "common", "hello", "嗨 {{name}}")
    en = _load(repo / "web/js/i18n/en.json")
    en["common"]["hello"] = "Hi"
    _dump(repo / "web/js/i18n/en.json", en)
    assert run(repo, "accept", "--lang", "en", "--keys", "common.hello") == 1
    assert (repo / tr.LOCK_PATH).read_text(encoding="utf-8") == before


# ─── 驗證器 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "text", "lang", "cid", "key", "ok"),
    [
        ("你好 {{name}}", "Hello {{name}}", "en", "web", "k", True),
        ("你好 {{ name }}", "Hello {{name}}", "en", "web", "k", True),
        ("你好 {{name}}", "Hello {name}", "en", "web", "k", False),
        ("你好 {{name}}", "Hello", "en", "web", "k", False),
        ("{a} 與 {a}", "{a} and", "en", "web", "k", False),
        ("逾時 {seconds} 秒", "Timeout {secs}", "en", "core/errors", "k", False),
        ("打開 <b>錢包</b>", "Open <b>wallet</b>", "en", "web", "k", True),
        ("打開 <b>錢包</b>", "Open wallet", "en", "web", "k", False),
        ("打開 <b>錢包</b>", "Open <i>wallet</i>", "en", "web", "k", False),
        ("見 https://x.io/a", "See https://x.io/a.", "en", "web", "k", True),
        ("見 https://x.io/a", "See https://x.io/b", "en", "web", "k", False),
        ("儲存", "   ", "en", "web", "k", False),
        ("儲存", ["Save"], "en", "web", "k", False),
        ("儲存", "Save 儲存", "en", "web", "k", False),
        ("儲存", "Save：", "en", "web", "k", False),  # 全形標點也算 CJK 區
        ("儲存", "Сохранить", "en", "web", "k", False),
        ("儲存", "Сохранить", "ru", "web", "k", True),
        ("儲存", "Сохранить 儲存", "ru", "web", "k", False),
        (
            "中文",
            "中文",
            "en",
            "web",
            "safety.languageToggle",
            True,
        ),  # 刻意混用的允許清單
        (
            "請用繁體中文回答。",
            "Answer in English.",
            "en",
            "core/llm_sections",
            "k",
            True,
        ),
        (
            "請用繁體中文回答。",
            "Answer in Chinese.",
            "en",
            "core/llm_sections",
            "k",
            False,
        ),
        (
            "請用繁體中文回答。",
            "Отвечайте на русском языке.",
            "ru",
            "core/llm_sections",
            "k",
            True,
        ),
        (
            "請用繁體中文回答。",
            "Отвечайте на китайском.",
            "ru",
            "core/llm_sections",
            "k",
            False,
        ),
        # UI catalog 裡的語言名稱是標籤，不是「回答用什麼語言」
        ("繁體中文", "Traditional Chinese", "en", "web", "k", True),
    ],
)
def test_validate(source, text, lang, cid, key, ok):
    assert (tr.validate(source, text, lang, cid, key) == []) is ok


# ─── translate ──────────────────────────────────────────────────────────────


def test_translate_refuses_without_lock(repo, no_http):
    assert run(repo, "translate", env=ENV) == 2


def test_translate_prunes_extra_keys_without_llm(repo, no_http):
    run(repo, "init-lock")
    zh_cn_before = (repo / "web/js/i18n/zh-CN.json").read_text(encoding="utf-8")
    zh = _load(repo / "web/js/i18n/zh-TW.json")
    del zh["common"]["open"]
    _dump(repo / "web/js/i18n/zh-TW.json", zh)
    assert run(repo, "check") == 1  # extra + lock-orphans

    assert run(repo, "translate") == 0  # 沒有待翻 key：不需要 env、不打 LLM
    for lang in ("en", "ru"):
        assert "open" not in _load(repo / f"web/js/i18n/{lang}.json")["common"]
        assert "common.open" not in _load(repo / tr.LOCK_PATH)[lang]["web"]
    assert run(repo, "check") == 0
    # metadata 與其他語系區段原封不動
    errors = _load(repo / "core/i18n/errors.json")
    assert list(errors["ru"]) == ["_todo_native_review", "a.timeout"]
    assert list(_load(repo / "core/i18n/llm_sections.json")) == [
        "_comment",
        "zh-TW",
        "zh-CN",
        "en",
        "ru",
    ]
    assert (repo / "web/js/i18n/zh-CN.json").read_text(encoding="utf-8") == zh_cn_before


def test_translate_writes_in_zh_tw_order_and_updates_lock(repo, fake_llm, capsys):
    calls, set_answer = fake_llm
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")  # stale
    zh = _load(repo / "web/js/i18n/zh-TW.json")
    zh["common"] = {
        "save": "存檔",
        "newKey": "新功能",
        "hello": "你好 {{name}}",
        "open": "打開 <b>錢包</b>",
    }
    _dump(repo / "web/js/i18n/zh-TW.json", zh)
    answers = {
        ("en", "common.save"): "Save file",
        ("en", "common.newKey"): "New feature",
        ("ru", "common.save"): "Сохранить файл",
        ("ru", "common.newKey"): "Новая функция",
    }
    set_answer(lambda lang, key, item: answers[(lang, key)])

    assert run(repo, "translate", "--lang", "en,ru", env=ENV) == 0

    for lang in ("en", "ru"):
        path = repo / f"web/js/i18n/{lang}.json"
        data = _load(path)
        assert _keys_in_order(data) == _keys_in_order(zh)
        assert data["common"]["newKey"] == answers[(lang, "common.newKey")]
        assert (
            path.read_text(encoding="utf-8")
            == json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        lock = _load(repo / tr.LOCK_PATH)[lang]["web"]
        assert lock["common.save"] == tr.fingerprint("存檔")
        assert lock["common.newKey"] == tr.fingerprint("新功能")
    assert run(repo, "check") == 0

    assert len(calls) == 2  # 每個語言一批
    first = calls[0]
    assert first["url"] == "https://llm.example/v1/chat/completions"
    assert first["headers"]["Authorization"] == f"Bearer {API_KEY}"
    system = first["payload"]["messages"][0]["content"]
    assert (
        "錢包 → wallet" in system
        and "{{name}}" in system
        and "investment advice" in system
    )
    items = json.loads(first["payload"]["messages"][1]["content"])["items"]
    assert list(items) == ["common.save", "common.newKey"]
    assert items["common.save"] == {
        "source": "存檔",
        "current": "Save",
    }  # stale 帶舊譯文
    assert items["common.newKey"] == {"source": "新功能"}
    assert "«вы»" in calls[1]["payload"]["messages"][0]["content"]
    out = capsys.readouterr()
    assert API_KEY not in out.out + out.err


def test_invalid_llm_output_is_not_written(repo, fake_llm, capsys):
    _, set_answer = fake_llm
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "hello", "嗨 {{name}}")
    set_answer(lambda lang, key, item: "Hi" if lang == "en" else "Привет 你好 {{name}}")

    assert run(repo, "translate", env=ENV) == 1

    assert _load(repo / "web/js/i18n/en.json")["common"]["hello"] == "Hello {{name}}"
    assert _load(repo / "web/js/i18n/ru.json")["common"]["hello"] == "Привет, {{name}}"
    ws = tr.Workspace(repo)
    assert ws.diff("en", "web").stale == ["common.hello"]
    assert ws.diff("ru", "web").stale == ["common.hello"]
    err = capsys.readouterr().out
    assert "common.hello" in err and "placeholder" in err


def test_llm_sections_prompt_and_language_name_check(repo, fake_llm):
    calls, set_answer = fake_llm
    run(repo, "init-lock")
    path = repo / "core/i18n/llm_sections.json"
    data = _load(path)
    data["zh-TW"]["nudge.lang"] = "請直接用繁體中文回答。"
    _dump(path, data)
    set_answer(
        lambda lang, key, item: (
            "Answer directly in Chinese."
            if lang == "en"
            else "Отвечайте сразу на русском языке."
        )
    )

    assert run(repo, "translate", env=ENV) == 1  # en 被擋
    assert "instructions" in calls[0]["payload"]["messages"][0]["content"]
    data = _load(path)
    assert data["en"]["nudge.lang"] == "Answer in English."
    assert data["ru"]["nudge.lang"] == "Отвечайте сразу на русском языке."
    assert list(data["ru"])[0] == "_todo_native_review"


def test_missing_key_in_llm_output_stays_pending(repo, fake_llm):
    _, set_answer = fake_llm
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    set_answer(lambda lang, key, item: None)
    assert run(repo, "translate", "--lang", "en", env=ENV) == 1
    assert tr.Workspace(repo).diff("en", "web").stale == ["common.save"]


def test_broken_batch_is_split_until_it_parses(repo, fake_llm):
    calls, set_answer = fake_llm
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    _set_zh_tw(repo, "nav", "wallet", "我的錢包")

    def raw(user):
        if len(user["items"]) > 1:
            return _ok('{"common.save": "Save fi', finish="length")  # 被截斷
        (key,) = user["items"]
        return _ok(
            json.dumps(
                {key: {"common.save": "Save file", "nav.wallet": "My wallet"}[key]}
            )
        )

    set_answer(raw=raw)
    assert run(repo, "translate", "--lang", "en", env=ENV) == 0
    assert len(calls) == 3
    en = _load(repo / "web/js/i18n/en.json")
    assert en["common"]["save"] == "Save file" and en["nav"]["wallet"] == "My wallet"


def test_limit_translates_only_first_pending_keys(repo, fake_llm):
    _, set_answer = fake_llm
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    _set_zh_tw(repo, "nav", "wallet", "我的錢包")
    set_answer(
        lambda lang, key, item: {"common.save": "Save file", "nav.wallet": "My wallet"}[
            key
        ]
    )
    assert run(repo, "translate", "--lang", "en", "--limit", "1", env=ENV) == 0
    assert tr.Workspace(repo).diff("en", "web").stale == ["nav.wallet"]


def test_missing_env_is_a_clear_error_and_nothing_is_called(repo, no_http, capsys):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    assert run(repo, "translate", env={"I18N_LLM_MODEL": "x"}) == 2
    err = capsys.readouterr().err
    assert (
        "I18N_LLM_BASE_URL" in err
        and "I18N_LLM_API_KEY" in err
        and "I18N_LLM_MODEL" not in err
    )


def test_dry_run_calls_nothing_and_writes_nothing(repo, no_http, capsys):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    snapshot = {p: p.read_bytes() for p in repo.rglob("*.json")}
    assert run(repo, "translate", "--dry-run") == 0
    assert {p: p.read_bytes() for p in repo.rglob("*.json")} == snapshot
    assert "web:common.save" in capsys.readouterr().out


def test_http_error_stops_and_never_prints_the_key(repo, monkeypatch, capsys):
    run(repo, "init-lock")
    _set_zh_tw(repo, "common", "save", "存檔")
    monkeypatch.setattr(tr, "RETRY_DELAY", 0)
    monkeypatch.setattr(
        tr.httpx,
        "post",
        lambda *a, **k: _Resp(status=401, text=f'{{"error": "bad key {API_KEY}"}}'),
    )
    assert run(repo, "translate", env=ENV) == 1
    out = capsys.readouterr()
    assert "401" in out.err and API_KEY not in out.out + out.err
    assert tr.Workspace(repo).diff("en", "web").stale == ["common.save"]


def test_config_repr_hides_key():
    cfg = tr.LLMConfig.from_env(
        {**ENV, "I18N_LLM_BASE_URL": "https://llm.example/v1/chat/completions"}
    )
    assert API_KEY not in repr(cfg)
    assert cfg.base_url == "https://llm.example/v1"


# ─── repo 上的真實檔案 ──────────────────────────────────────────────────────


def test_glossary_seed_is_well_formed():
    glossary = tr.load_glossary(ROOT)
    for term in (
        "CryptoMind",
        "帳本",
        "早報",
        "行事曆",
        "判斷評分",
        "詐騙檢查",
        "自帶金鑰",
        "訪客模式",
        "同意卡",
        "錢包",
        "會員",
        "Premium",
    ):
        assert term in glossary, term
    for term, entry in glossary.items():
        assert entry.get("en", "").strip() and entry.get("ru", "").strip(), term
        assert not tr.CJK_RE.search(entry["en"]) and not tr.CYRILLIC_RE.search(
            entry["en"]
        ), term
        assert not tr.CJK_RE.search(entry["ru"]), term


def test_repo_translation_lock_is_in_sync():
    """zh-TW 改了或加了 key 卻沒跑 translate／accept → 這條紅。訊息裡有該跑的指令。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "i18n" / "translate.py"), "check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]


def test_validator_rejects_injected_html_attributes():
    """review：只比標籤名時，<b onclick=…> 會被當成 <b> 放行。"""
    src = "請綁定<b>錢包</b>"
    ok = tr.validate(src, "Bind your <b>wallet</b>", "en", "web", "k")
    bad = tr.validate(src, 'Bind your <b onclick="x()">wallet</b>', "en", "web", "k")
    assert ok == [] and bad


@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://api.example.com/v1", True),
        ("http://localhost:4000/v1", True),
        ("http://127.0.0.1:4000/v1", True),
        ("http://api.example.com/v1", False),
    ],
)
def test_remote_endpoint_requires_https(url, ok):
    env = {"I18N_LLM_BASE_URL": url, "I18N_LLM_API_KEY": "k", "I18N_LLM_MODEL": "m"}
    if ok:
        assert tr.LLMConfig.from_env(env).base_url.startswith("http")
    else:
        with pytest.raises(tr.ConfigError):
            tr.LLMConfig.from_env(env)


def test_check_fails_on_existing_translation_that_lost_a_placeholder(repo):
    """ru 曾有 13 句掉了 {sym}／{category}，invalid 只提示不擋 → 一路上了線。"""
    run(repo, "init-lock")
    ru = _load(repo / "core/i18n/errors.json")
    ru["ru"]["a.timeout"] = "Тайм-аут"
    _dump(repo / "core/i18n/errors.json", ru)
    ws = tr.Workspace(repo)
    assert [k for k, _ in ws.diff("ru", "core/errors").invalid] == ["a.timeout"]
    assert run(repo, "check") == 1
