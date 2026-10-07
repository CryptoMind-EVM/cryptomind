#!/usr/bin/env python3
"""zh-TW 單一來源 → en／ru 翻譯管線（i18n 第二步）。

zh-TW 是唯一手改的語系。zh-CN 由 OpenCC 產生（scripts/i18n/gen_zh_cn.mjs），
en／ru 只翻「新增」或「zh-TW 原文改過」的 key：LLM 起草，當一般 PR diff 審。

怎麼知道哪些要重翻
    scripts/i18n/translation-lock.json 記下每個 (語言, catalog, key) 在譯文
    被審過當下的 zh-TW 指紋（sha256 前 16 碼）。zh-TW 改了 → 指紋對不上 → stale。
    lock 放在 catalog 目錄外面：core/i18n 與 web/js/i18n 底下的 *.json 會被測試當成 catalog 掃。

Catalogs
    web             web/js/i18n/<lang>.json（巢狀，key 攤平成 a.b.c）
    core/<name>     core/i18n/<name>.json 的 "<lang>" 區段（平的 key）
    頂層底線開頭的 key（_todo_native_review、_comment）是 metadata：不翻、不刪、不算 extra。

指令
    python scripts/i18n/translate.py status [--lang en,ru] [--full]
    python scripts/i18n/translate.py check                     # 有 missing/stale/extra 就 exit 1
    python scripts/i18n/translate.py init-lock [--force]       # 一次性：現有譯文全部視為已審
    python scripts/i18n/translate.py accept --lang en --keys web:common.save,core/errors:analysis.timeout
    python scripts/i18n/translate.py accept --lang en,ru --all-stale   # 手改譯文後標記為已審
    python scripts/i18n/translate.py translate --lang en,ru [--limit N] [--dry-run]

    translate 會依 zh-TW 的 key 順序重寫 en／ru，順便刪掉 zh-TW 已經沒有的 key。
    --dry-run 只列出要翻的 key，不呼叫 LLM、不寫檔。

環境變數（只有 translate 真的要呼叫 LLM 時才讀；沒有預設值）
    I18N_LLM_BASE_URL   OpenAI 相容端點，例如 https://api.deepseek.com/v1
    I18N_LLM_API_KEY    金鑰（不會被印出）
    I18N_LLM_MODEL      例如 deepseek-chat。別用推理模型：思考吃掉輸出額度，JSON 會被截斷。

完整流程見 docs/i18n.md。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
SOURCE_LANG = "zh-TW"
TARGET_LANGS = ("en", "ru")
LOCK_PATH = Path("scripts/i18n/translation-lock.json")
GLOSSARY_PATH = Path("scripts/i18n/glossary.json")
CATALOG_IDS = ("web", "core/errors", "core/llm_sections", "core/ui_messages")
# 送進 LLM 的指令文字，不是 UI：要保住指令語意，「用繁體中文回答」要換成目標語言
LLM_CATALOGS = frozenset({"core/llm_sections"})
LOCK_COMMENT = (
    "由 scripts/i18n/translate.py 維護，不要手改。"
    "值是譯文被審過當下 zh-TW 原文的 sha256 前 16 碼；對不上 = zh-TW 改過、譯文要重看。"
)

BATCH_SIZE = 40
MAX_TOKENS = 8192
HTTP_TIMEOUT = 180.0
RETRY_DELAY = 2.0
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
LIST_LIMIT = 20
ENV_VARS = ("I18N_LLM_BASE_URL", "I18N_LLM_API_KEY", "I18N_LLM_MODEL")
LANG_NAMES = {"en": "English", "ru": "Russian"}

# 與 tests/test_i18n_completeness.py::TestLocaleScriptSanity 同一組字元範圍
CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
FORBIDDEN_SCRIPTS = {"en": (CJK_RE, CYRILLIC_RE), "ru": (CJK_RE,)}
# 刻意混用：語言切換鈕顯示的是「要切過去的那個語言」的自稱（en 介面顯示「中文」）
SCRIPT_ALLOW = frozenset({("en", "web", "safety.languageToggle")})

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}|\{(\w+)\}")
TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^<>]*?(/?)>")
URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
OUTPUT_LANG_MARKERS = ("繁體中文", "繁中")
TARGET_LANG_NAME_RE = {
    "en": re.compile(r"\bEnglish\b"),
    "ru": re.compile(r"русск", re.IGNORECASE),
}


class ConfigError(RuntimeError):
    """環境變數沒設好。"""


class LLMError(RuntimeError):
    """端點／認證層級的錯誤：整個翻譯流程停下來。"""


class BadOutput(ValueError):
    """這一批的輸出不是合法 JSON 物件（多半是被截斷）：拆小再送。"""


# ─── catalog I/O ────────────────────────────────────────────────────────────


def fingerprint(text) -> str:
    raw = (
        text
        if isinstance(text, str)
        else json.dumps(text, ensure_ascii=False, sort_keys=True)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_meta(key: str) -> bool:
    return key.startswith("_")


def flatten(tree: dict, prefix: str = "") -> dict:
    """巢狀 dict → {"a.b.c": 值}；頂層底線 key 是 metadata，略過。"""
    flat = {}
    for key, value in tree.items():
        if not prefix and is_meta(key):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def rebuild(order: dict, values: dict, prefix: str = "") -> dict:
    """照 zh-TW 的結構與順序，用攤平的 values 組回巢狀 dict；values 沒有的 key 略過。"""
    out = {}
    for key, node in order.items():
        if not prefix and is_meta(key):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(node, dict):
            sub = rebuild(node, values, path)
            if sub:
                out[key] = sub
        elif path in values:
            out[key] = values[path]
    return out


def _catalog_file(root: Path, cid: str, lang: str) -> Path:
    if cid == "web":
        return root / "web" / "js" / "i18n" / f"{lang}.json"
    return root / "core" / "i18n" / f"{cid.split('/', 1)[1]}.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data, sort_keys: bool = False) -> bool:
    """跟既有 catalog 同格式（2 格縮排、不跳脫非 ASCII、結尾換行）；內容沒變就不寫。"""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def read_locale(root: Path, cid: str, lang: str) -> dict:
    data = _read_json(_catalog_file(root, cid, lang))
    return data if cid == "web" else data.get(lang, {})


def write_locale(root: Path, cid: str, lang: str, tree: dict) -> bool:
    path = _catalog_file(root, cid, lang)
    if cid == "web":
        return _write_json(path, tree)
    data = _read_json(path)
    data[lang] = tree  # 覆寫既有 key 不改位置：其他語系區段與 _comment 原樣保留
    return _write_json(path, data)


def load_glossary(root: Path) -> dict:
    path = root / GLOSSARY_PATH
    data = _read_json(path) if path.exists() else {}
    return {term: entry for term, entry in data.items() if not is_meta(term)}


# ─── 驗證 ───────────────────────────────────────────────────────────────────


def placeholders(text: str) -> Counter:
    return Counter(
        f"{{{{{a}}}}}" if a else f"{{{b}}}" for a, b in PLACEHOLDER_RE.findall(text)
    )


def html_tags(text: str) -> Counter:
    """整個標籤原樣比（含屬性）：只比標籤名的話，<b onclick="…"> 會被當成 <b> 放行。"""
    return Counter(re.sub(r"\s+", " ", m.group(0)) for m in TAG_RE.finditer(text))


def urls(text: str) -> Counter:
    return Counter(u.rstrip(".,;:!?)'") for u in URL_RE.findall(text))


def validate(source: str, text, lang: str, cid: str, key: str) -> list[str]:
    """回傳譯文的問題清單；空清單 = 可以寫入。"""
    if not isinstance(text, str) or not text.strip():
        return ["空字串或不是字串"]
    errors = []
    if placeholders(text) != placeholders(source):
        errors.append(
            f"placeholder 不一致（原文 {sorted(placeholders(source).elements())}，"
            f"譯文 {sorted(placeholders(text).elements())}）"
        )
    if html_tags(text) != html_tags(source):
        errors.append("HTML 標籤不一致")
    if urls(text) != urls(source):
        errors.append("URL 不一致")
    if (lang, cid, key) not in SCRIPT_ALLOW:
        for pattern in FORBIDDEN_SCRIPTS.get(lang, ()):
            hit = pattern.search(text)
            if hit:
                errors.append(f"含不該出現的文字 {hit.group()!r}")
    if (
        cid in LLM_CATALOGS
        and any(marker in source for marker in OUTPUT_LANG_MARKERS)
        and not TARGET_LANG_NAME_RE[lang].search(text)
    ):
        errors.append("原文指定回答語言（繁體中文），譯文要改成目標語言")
    return errors


# ─── 工作區：catalog + lock 的記憶體狀態 ─────────────────────────────────────


@dataclass
class Diff:
    missing: list = field(default_factory=list)  # zh-TW 有、目標沒有
    stale: list = field(default_factory=list)  # 指紋對不上或 lock 沒記錄
    extra: list = field(default_factory=list)  # 目標有、zh-TW 沒有
    orphans: list = field(default_factory=list)  # lock 裡已不存在的 key
    invalid: list = field(default_factory=list)  # (key, 原因)：既有譯文沒過驗證

    @property
    def blocking(self) -> int:
        return (
            len(self.missing)
            + len(self.stale)
            + len(self.extra)
            + len(self.orphans)
            + len(self.invalid)
        )


class Workspace:
    """一次讀進 zh-TW、目標語系與 lock；改動都在記憶體，save_* 才落地。"""

    def __init__(self, root: Path):
        self.root = root
        self.lock_path = root / LOCK_PATH
        self.source_tree = {
            cid: read_locale(root, cid, SOURCE_LANG) for cid in CATALOG_IDS
        }
        self.source = {cid: flatten(tree) for cid, tree in self.source_tree.items()}
        self.lock = _read_json(self.lock_path) if self.lock_path.exists() else {}
        self._targets: dict[tuple[str, str], tuple[dict, dict]] = {}

    def target(self, lang: str, cid: str) -> dict:
        if (lang, cid) not in self._targets:
            tree = read_locale(self.root, cid, lang)
            self._targets[(lang, cid)] = (tree, flatten(tree))
        return self._targets[(lang, cid)][1]

    def locked(self, lang: str, cid: str) -> dict:
        return self.lock.get(lang, {}).get(cid, {})

    def is_current(self, lang: str, cid: str, key: str) -> bool:
        return self.locked(lang, cid).get(key) == fingerprint(self.source[cid][key])

    def pending(self, lang: str, cid: str) -> list[str]:
        """missing + stale，照 zh-TW 順序。"""
        tgt = self.target(lang, cid)
        return [
            k
            for k in self.source[cid]
            if k not in tgt or not self.is_current(lang, cid, k)
        ]

    def diff(self, lang: str, cid: str) -> Diff:
        src, tgt, lock = (
            self.source[cid],
            self.target(lang, cid),
            self.locked(lang, cid),
        )
        d = Diff()
        for key, text in src.items():
            if key not in tgt:
                d.missing.append(key)
            elif not self.is_current(lang, cid, key):
                d.stale.append(key)
            else:
                errors = validate(text, tgt[key], lang, cid, key)
                if errors:
                    d.invalid.append((key, "；".join(errors)))
        d.extra = [k for k in tgt if k not in src]
        d.orphans = sorted(k for k in lock if k not in src or k not in tgt)
        return d

    def mark_reviewed(self, lang: str, cid: str, key: str) -> None:
        entries = self.lock.setdefault(lang, {}).setdefault(cid, {})
        entries[key] = fingerprint(self.source[cid][key])

    def set_translation(self, lang: str, cid: str, key: str, text: str) -> None:
        self.target(lang, cid)[key] = text
        self.mark_reviewed(lang, cid, key)

    def save_targets(self, lang: str) -> list[str]:
        """照 zh-TW 順序重寫目標 catalog（zh-TW 沒有的 key 就此刪掉）。回傳有變動的 catalog。"""
        changed = []
        for cid in CATALOG_IDS:
            self.target(lang, cid)
            tree, flat = self._targets[(lang, cid)]
            meta = {k: v for k, v in tree.items() if is_meta(k)}
            if write_locale(
                self.root, cid, lang, {**meta, **rebuild(self.source_tree[cid], flat)}
            ):
                changed.append(cid)
        return changed

    def save_lock(self) -> bool:
        """只留 zh-TW 與目標都還在的 key；排序輸出讓 diff 最小。"""
        clean: dict = {"_comment": LOCK_COMMENT}
        for lang in (k for k in self.lock if not is_meta(k)):
            clean[lang] = {}
            for cid in CATALOG_IDS:
                src, tgt = self.source[cid], self.target(lang, cid)
                kept = {
                    k: v
                    for k, v in self.locked(lang, cid).items()
                    if k in src and k in tgt
                }
                if kept:
                    clean[lang][cid] = kept
        self.lock = clean
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        return _write_json(self.lock_path, clean, sort_keys=True)


# ─── status / check / init-lock / accept ────────────────────────────────────


def _print_list(label: str, items: list, full: bool) -> None:
    if not items:
        return
    shown = items if full else items[:LIST_LIMIT]
    print(f"    {label}（{len(items)}）")
    for item in shown:
        print(f"      {item}")
    if len(items) > len(shown):
        print(f"      … 還有 {len(items) - len(shown)} 個（加 --full 全列）")


def report(ws: Workspace, langs: tuple, full: bool) -> int:
    """印出每個語言 × catalog 的狀態，回傳擋 check 的問題總數。"""
    total = 0
    for lang in langs:
        print(f"[{lang}]")
        for cid in CATALOG_IDS:
            d = ws.diff(lang, cid)
            total += d.blocking
            print(
                f"  {cid:<18} missing {len(d.missing):>4}  stale {len(d.stale):>4}  "
                f"extra {len(d.extra):>4}  lock-orphans {len(d.orphans):>3}  invalid {len(d.invalid):>3}"
            )
            _print_list("missing", d.missing, full)
            _print_list("stale", d.stale, full)
            _print_list("extra", d.extra, full)
            _print_list("lock-orphans", d.orphans, full)
            _print_list(
                "invalid（既有譯文沒過驗證）",
                [f"{k}：{r}" for k, r in d.invalid],
                full,
            )
    return total


def cmd_check(ws: Workspace, langs: tuple, full: bool) -> int:
    if report(ws, langs, full) == 0:
        print("OK：en／ru 與 zh-TW 同步")
        return 0
    print(
        "\n有 key 沒同步。處理方式：\n"
        "  新增／改了 zh-TW → python scripts/i18n/translate.py translate --lang en,ru\n"
        "  手動改好譯文     → python scripts/i18n/translate.py accept --lang en,ru --all-stale\n"
        "  只剩 extra／lock-orphans → translate（不需要 LLM，會直接刪除）\n"
        "  invalid          → 直接改該語系 JSON 的譯文（lock 記的是原文指紋，不用 accept）"
    )
    return 1


def cmd_init_lock(ws: Workspace, langs: tuple, force: bool) -> int:
    if ws.lock_path.exists() and not force:
        print(
            f"{LOCK_PATH} 已存在；init-lock 只用在第一次建立，要覆寫請加 --force",
            file=sys.stderr,
        )
        return 2
    for lang in langs:
        ws.lock[lang] = {}
        for cid in CATALOG_IDS:
            tgt = ws.target(lang, cid)
            for key in ws.source[cid]:
                if key in tgt:
                    ws.mark_reviewed(lang, cid, key)
    ws.save_lock()
    counts = {
        lang: sum(len(v) for v in ws.lock.get(lang, {}).values()) for lang in langs
    }
    print(
        f"已寫入 {LOCK_PATH}："
        + "、".join(f"{lang} {n} 個 key" for lang, n in counts.items())
    )
    return 0


def resolve_key(ws: Workspace, spec: str) -> tuple[str, str]:
    """'catalog:key' 或唯一的 'key' → (catalog, key)。"""
    if ":" in spec:
        cid, key = spec.split(":", 1)
        if cid not in CATALOG_IDS or key not in ws.source[cid]:
            raise ValueError(f"{spec}：zh-TW 沒有這個 key")
        return cid, key
    hits = [cid for cid in CATALOG_IDS if spec in ws.source[cid]]
    if not hits:
        raise ValueError(f"{spec}：zh-TW 沒有這個 key")
    if len(hits) > 1:
        raise ValueError(f"{spec}：{'、'.join(hits)} 都有，請寫成 catalog:key")
    return hits[0], spec


def cmd_accept(ws: Workspace, langs: tuple, keys: list, all_stale: bool) -> int:
    """把目前的譯文標記為已審（手改譯文之後用）。有任何問題就整批不寫。"""
    if not keys and not all_stale:
        print("accept 需要 --keys 或 --all-stale", file=sys.stderr)
        return 2
    errors, accepted, resolved = [], [], []
    for spec in keys:
        try:
            resolved.append(resolve_key(ws, spec))
        except ValueError as exc:
            errors.append(str(exc))
    for lang in langs:
        targets = resolved
        if all_stale:
            targets = [
                (cid, k) for cid in CATALOG_IDS for k in ws.diff(lang, cid).stale
            ]
        for cid, key in targets:
            tgt = ws.target(lang, cid)
            if key not in tgt:
                errors.append(
                    f"[{lang}] {cid}:{key}：目標語系沒有這個 key（missing 要用 translate）"
                )
                continue
            problems = validate(ws.source[cid][key], tgt[key], lang, cid, key)
            if problems:
                errors.append(f"[{lang}] {cid}:{key}：{'；'.join(problems)}")
                continue
            accepted.append((lang, cid, key))
    if errors:
        print("沒有寫入 lock：\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    for lang, cid, key in accepted:
        ws.mark_reviewed(lang, cid, key)
    ws.save_lock()
    print(f"已標記為已審：{len(accepted)} 個（{', '.join(langs)}）")
    return 0


# ─── LLM ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)

    @classmethod
    def from_env(cls, env) -> LLMConfig:
        missing = [name for name in ENV_VARS if not (env.get(name) or "").strip()]
        if missing:
            raise ConfigError(
                f"缺少環境變數 {'、'.join(missing)}（沒有預設值，見 docs/i18n.md）"
            )
        base_url = env["I18N_LLM_BASE_URL"].strip().rstrip("/")
        base_url = base_url.removesuffix("/chat/completions")
        if not base_url.startswith(("https://", "http://")):
            raise ConfigError(
                "I18N_LLM_BASE_URL 要是 http(s):// 開頭的 OpenAI 相容端點"
            )
        # 金鑰放在 Authorization header：非本機一律要 https，免得明文送出
        host = (urlsplit(base_url).hostname or "").lower()
        if base_url.startswith("http://") and host not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ConfigError(
                "I18N_LLM_BASE_URL 非本機端點必須用 https://（金鑰會放在 header）"
            )
        return cls(
            base_url=base_url,
            model=env["I18N_LLM_MODEL"].strip(),
            api_key=env["I18N_LLM_API_KEY"].strip(),
        )

    def redact(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text


_STYLE = {
    "en": "Use US English. Title Case only for short navigation labels, buttons and headings; sentence case elsewhere.",
    "ru": "Address the user formally with «вы» (lowercase), never «ты». Buttons use the infinitive (e.g. «Сохранить»).",
}
_LLM_LANGUAGE = {"en": "English", "ru": "Russian («на русском языке»)"}


def system_prompt(lang: str, cid: str, glossary: dict) -> str:
    language = LANG_NAMES[lang]
    terms = []
    for term, entry in glossary.items():
        if entry.get(lang):
            note = f" ({entry['note']})" if entry.get("note") else ""
            terms.append(f"   - {term} → {entry[lang]}{note}")
    rules = [
        "You localize UI strings for CryptoMind, an AI analysis app for crypto and stock markets, "
        f"from Traditional Chinese (Taiwan, zh-TW) into {language}.",
        "",
        'Input: a JSON object whose "items" maps each key to {"source": zh-TW text}. When the source was '
        'edited after an earlier translation, the item also has "current": that earlier translation.',
        "Output: ONLY a JSON object mapping every input key to its translated string. Same keys, nothing else.",
        "",
        "Rules:",
        "1. Concise, natural UI wording (buttons, labels, toasts, hints). Do not add explanations, notes or "
        f"surrounding quotes. Use {language} punctuation, never full-width Chinese punctuation such as ，。：；！？（）「」.",
        "2. Keep exactly as written: placeholders such as {name} and {{name}} (never translate or rename what is "
        "inside the braces), HTML tags and attributes, emoji, URLs, Markdown markers (#, **, -, `), code and tool "
        "names (load_skill, web_search, any snake_case or camelCase identifier), ticker symbols, and the names "
        "CryptoMind, USDC, Base, TON, EVM, BYOK, Telegram.",
        "3. Use the glossary wording for these product terms, inflected as grammar requires:",
        *terms,
        "4. Compliance tone: CryptoMind provides data analysis, not financial or investment advice. Never turn "
        "neutral wording into trading signals, buy/sell recommendations, calls to trade or promises of returns; "
        'keep disclaimers such as "not investment advice" intact. Write "signal" only where the source '
        "literally says 訊號 or 信號.",
        '5. When "current" is given, revise it to match the new source and keep its wording wherever the '
        "meaning did not change.",
        f"6. {_STYLE[lang]}",
    ]
    if cid in LLM_CATALOGS:
        rules.append(
            "7. These strings are instructions and section headers fed to an LLM, not UI text. Keep the "
            "instruction semantics precise: preserve every constraint, negation, condition and the order of "
            "steps; do not shorten, soften or add anything. If the source names the language the answer must "
            f"be written in (e.g. 繁體中文), name {_LLM_LANGUAGE[lang]} instead."
        )
    return "\n".join(rules)


def build_messages(lang: str, cid: str, items: dict, glossary: dict) -> list[dict]:
    user = {"target_language": LANG_NAMES[lang], "catalog": cid, "items": items}
    return [
        {"role": "system", "content": system_prompt(lang, cid, glossary)},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=1)},
    ]


def chat_completion(cfg: LLMConfig, messages: list[dict]) -> tuple[str, str | None]:
    """呼叫 OpenAI 相容 chat completions；429／5xx／連線錯誤重試兩次。"""
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    error = "未知錯誤"
    for attempt in range(3):
        if attempt:
            time.sleep(RETRY_DELAY * attempt)
        try:
            resp = httpx.post(
                f"{cfg.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            error = f"連線失敗：{type(exc).__name__}"
            continue
        if resp.status_code == 200:
            try:
                choice = resp.json()["choices"][0]
                content = choice["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError):
                raise BadOutput("回應不是 chat completions 格式") from None
            return content, choice.get("finish_reason")
        error = f"HTTP {resp.status_code}：{cfg.redact(resp.text[:300])}"
        if resp.status_code not in RETRY_STATUS:
            break
    raise LLMError(error)


def parse_output(content: str, finish_reason: str | None) -> dict:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        if finish_reason == "length":
            raise BadOutput("輸出被截斷（finish_reason=length）") from None
        raise BadOutput(f"輸出不是 JSON：{exc.msg}") from None
    if not isinstance(data, dict):
        raise BadOutput("輸出不是 JSON 物件")
    return data


def translate_items(
    cfg: LLMConfig, lang: str, cid: str, items: dict, glossary: dict
) -> tuple[dict, dict]:
    """回傳 (譯文, 失敗原因)。輸出壞掉就對半拆重送，拆到單一 key 仍壞才記為失敗。"""
    try:
        content, finish = chat_completion(
            cfg, build_messages(lang, cid, items, glossary)
        )
        return parse_output(content, finish), {}
    except BadOutput as exc:
        if len(items) == 1:
            return {}, {key: str(exc) for key in items}
    keys = list(items)
    results, failures = {}, {}
    for part in (keys[: len(keys) // 2], keys[len(keys) // 2 :]):
        got, failed = translate_items(
            cfg, lang, cid, {k: items[k] for k in part}, glossary
        )
        results.update(got)
        failures.update(failed)
    return results, failures


# ─── translate ──────────────────────────────────────────────────────────────


def _plan(ws: Workspace, lang: str, limit: int | None) -> list[tuple[str, str]]:
    work = [(cid, key) for cid in CATALOG_IDS for key in ws.pending(lang, cid)]
    return work[:limit] if limit is not None else work


def _items(ws: Workspace, lang: str, cid: str, keys: list[str]) -> dict:
    tgt = ws.target(lang, cid)
    items = {}
    for key in keys:
        item = {"source": ws.source[cid][key]}
        if key in tgt:
            item["current"] = tgt[key]
        items[key] = item
    return items


def _translate_lang(ws, cfg, lang, work, glossary, batch_size) -> tuple[int, list[str]]:
    """翻一個語言的所有待辦；回傳 (寫入數, 被擋下的 key 與原因)。LLMError 往上拋。"""
    written, rejected = 0, []
    for cid in CATALOG_IDS:
        keys = [k for c, k in work if c == cid]
        batches = [keys[i : i + batch_size] for i in range(0, len(keys), batch_size)]
        for n, batch in enumerate(batches, 1):
            print(
                f"[{lang}] {cid} 第 {n}/{len(batches)} 批（{len(batch)} 個 key）",
                flush=True,
            )
            results, failures = translate_items(
                cfg, lang, cid, _items(ws, lang, cid, batch), glossary
            )
            for key in batch:
                text = results.get(key)
                if key in failures:
                    problems = [failures[key]]
                elif text is None:
                    problems = ["輸出缺這個 key"]
                else:
                    problems = validate(ws.source[cid][key], text, lang, cid, key)
                if problems:
                    rejected.append(f"{cid}:{key}：{'；'.join(problems)}")
                    continue
                ws.set_translation(lang, cid, key, text)
                written += 1
    return written, rejected


def _dry_run(
    ws: Workspace, langs: tuple, plans: dict, batch_size: int, full: bool
) -> int:
    for lang in langs:
        work = plans[lang]
        per_catalog = Counter(cid for cid, _ in work)
        batches = sum((n + batch_size - 1) // batch_size for n in per_catalog.values())
        print(f"[{lang}] 要翻 {len(work)} 個 key，約 {batches} 次 LLM 呼叫")
        _print_list("pending", [f"{cid}:{key}" for cid, key in work], full)
        extra = [f"{cid}:{k}" for cid in CATALOG_IDS for k in ws.diff(lang, cid).extra]
        _print_list("extra（會刪掉）", extra, full)
    print("（dry-run：沒有呼叫 LLM，也沒有寫檔）")
    return 0


def cmd_translate(ws: Workspace, langs: tuple, args, env) -> int:
    if not ws.lock_path.exists():
        print(
            f"找不到 {LOCK_PATH}：先跑 init-lock，不然所有 key 都會被當成 stale 重翻",
            file=sys.stderr,
        )
        return 2
    plans = {lang: _plan(ws, lang, args.limit) for lang in langs}
    if args.dry_run:
        return _dry_run(ws, langs, plans, args.batch_size, args.full)
    cfg = None
    if any(plans.values()):
        try:
            cfg = LLMConfig.from_env(env)
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    glossary = load_glossary(ws.root)
    # aborted：端點壞了，後面的語言不再呼叫；failed：只影響 exit code
    aborted = failed = False
    for lang in langs:
        written, rejected = 0, []
        if plans[lang] and not aborted:
            try:
                written, rejected = _translate_lang(
                    ws, cfg, lang, plans[lang], glossary, args.batch_size
                )
            except LLMError as exc:
                print(f"[{lang}] LLM 呼叫失敗，停止：{exc}", file=sys.stderr)
                aborted = failed = True
        changed = ws.save_targets(lang)
        print(
            f"[{lang}] 寫入 {written} 個；更動的 catalog：{', '.join(changed) or '無'}"
        )
        if rejected:
            failed = True
            _print_list("沒寫入（仍是 missing/stale）", rejected, full=True)
    ws.save_lock()
    return 1 if failed else 0


# ─── CLI ────────────────────────────────────────────────────────────────────


def _langs(value: str) -> tuple:
    langs = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    unknown = [lang for lang in langs if lang not in TARGET_LANGS]
    if not langs or unknown:
        raise argparse.ArgumentTypeError(f"語言只能是 {', '.join(TARGET_LANGS)}")
    return langs


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("要是正整數")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="zh-TW → en／ru 翻譯管線（見 docs/i18n.md）"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("status", "列出 missing／stale／extra"),
        ("check", "同 status，有問題就 exit 1"),
        ("init-lock", "一次性：把現有譯文全部記為已審"),
        ("accept", "把目前的譯文標記為已審（手改後用）"),
        ("translate", "用 LLM 翻 missing／stale，並刪掉 extra"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--lang", type=_langs, default=TARGET_LANGS, help="逗號分隔，預設 en,ru"
        )
        p.add_argument("--full", action="store_true", help="清單不截斷")
        if name == "init-lock":
            p.add_argument("--force", action="store_true", help="覆寫既有 lock")
        if name == "accept":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument(
                "--keys", type=lambda v: [k.strip() for k in v.split(",") if k.strip()]
            )
            group.add_argument("--all-stale", action="store_true")
        if name == "translate":
            p.add_argument(
                "--limit", type=_positive, default=None, help="每個語言最多翻幾個 key"
            )
            p.add_argument("--dry-run", action="store_true", help="只列出要翻的 key")
            p.add_argument("--batch-size", type=_positive, default=BATCH_SIZE)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, env=None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace(root)
    if args.command == "status":
        report(ws, args.lang, args.full)
        return 0
    if args.command == "check":
        return cmd_check(ws, args.lang, args.full)
    if args.command == "init-lock":
        return cmd_init_lock(ws, args.lang, args.force)
    if args.command == "accept":
        return cmd_accept(ws, args.lang, args.keys or [], args.all_stale)
    return cmd_translate(ws, args.lang, args, os.environ if env is None else env)


if __name__ == "__main__":
    # Windows cp950 印不出俄文：換成 ? 而不是整支炸掉
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
