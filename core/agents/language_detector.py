"""Message-language detection heuristic.

Distinguishes the language of the USER'S MESSAGE from the UI locale, so an
English question gets an English answer even when the user's UI / browser
locale is zh-TW or ru. This is the server-side mirror of
``web/js/chat-analysis.js::detectMessageLanguage``.

Supported: ``zh-TW`` (CJK ideographs), ``ru`` (Cyrillic), ``en`` (default).
"""

from __future__ import annotations

import re

__all__ = ["detect_message_language", "SUPPORTED_LANGUAGES"]

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"zh-TW", "en", "ru"})

# CJK Unified Ideographs (U+4E00–U+9FFF) + CJK Extension A (U+3400–U+4DBF).
# Covers Modern + rarely-used Han chars without pulling in compatibility blocks
# (which would falsely flag kana / hangul as "Chinese").
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# Cyrillic block (U+0400–U+04FF): Russian, Belarusian, Ukrainian, Bulgarian, etc.
_CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04ff]")


def detect_message_language(text: str | None) -> str:
    """Return ``"zh-TW"`` for CJK ideographs, ``"ru"`` for Cyrillic, else ``"en"``.

    Empty / ``None`` → ``"en"`` (safe default that matches the original frontend
    fallback after the fix, and avoids the old ``zh-TW`` default that caused
    English queries to receive Chinese responses).
    """
    if not text:
        return "en"
    if _CJK_PATTERN.search(text):
        return "zh-TW"
    if _CYRILLIC_PATTERN.search(text):
        return "ru"
    return "en"


def normalize_language(language: str | None, fallback_text: str | None = None) -> str:
    """Return a supported language code.

    - If ``language`` is one of :data:`SUPPORTED_LANGUAGES`, use it as-is.
    - Otherwise detect from ``fallback_text`` (the user's message).
    - Last resort: ``"en"`` (never silently fall back to zh-TW).
    """
    if language in SUPPORTED_LANGUAGES:
        return language
    return detect_message_language(fallback_text)
