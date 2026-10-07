"""信件與結果頁。

- 只有 inline CSS；沒有圖片、腳本、外部樣式或追蹤像素。
- 早報內文（含使用者自填的分類、事件標題）與所有翻譯字串都 ``html.escape``。
- 每封早報：一鍵退訂（List-Unsubscribe＋List-Unsubscribe-Post，RFC 8058）、信末退訂連結、
  寄件者名稱＋實體地址（CAN-SPAM）。
"""

from __future__ import annotations

import hashlib
from html import escape
from typing import Optional

from core.email_brief import provider
from core.email_brief.provider import OutgoingEmail
from core.i18n import SUPPORTED_LANGUAGES
from core.i18n import t as _t

DEFAULT_LANGUAGE = "en"

_BODY = "margin:0;padding:0;background:#f4f5f7;"
_CARD = (
    "max-width:560px;margin:0 auto;padding:24px 20px;background:#ffffff;color:#1f2328;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    "font-size:15px;line-height:1.6;"
)
_H1 = "font-size:19px;line-height:1.4;margin:0 0 16px;color:#1f2328;"
_P = "margin:0 0 16px;white-space:pre-wrap;word-break:break-word;"
_HR = "border:none;border-top:1px solid #d0d7de;margin:24px 0 16px;"
_FOOT = "margin:0 0 8px;font-size:12px;line-height:1.5;color:#57606a;"
_LINK = "color:#0550ae;text-decoration:underline;"
_BUTTON = (
    "display:inline-block;padding:12px 20px;background:#0550ae;color:#ffffff;"
    "border-radius:8px;text-decoration:none;font-weight:600;"
)


def _lang(language: Optional[str]) -> str:
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def _tr(key: str, lang: str, **values) -> str:
    return _t(f"ui_messages.email_brief.{key}", lang, **values)


def _document(lang: str, title: str, inner: str, *, noindex: bool = False) -> str:
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    return (
        "<!doctype html>"
        f'<html lang="{escape(lang)}"><head><meta charset="utf-8">{robots}'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title></head>"
        f'<body style="{_BODY}"><div style="{_CARD}">{inner}</div></body></html>'
    )


def _identity_line() -> str:
    return f"{provider.sender_name()} · {provider.postal_address()}"


def brief_email(
    *,
    to: str,
    text: str,
    language: Optional[str],
    on_date: str,
    unsubscribe_url: str,
    manage_url: str,
    idempotency_key: Optional[str] = None,
) -> OutgoingEmail:
    """早報（純文字組稿）→ HTML＋純文字兩份。第一段當標題，其餘每段一個 <p>（保留換行與對齊）。"""
    lang = _lang(language)
    subject = _tr("subject", lang, date=on_date)
    blocks = [b.strip("\n") for b in text.strip().split("\n\n") if b.strip()]
    title, rest = (blocks[0], blocks[1:]) if blocks else (subject, [])
    body = "".join(f'<p style="{_P}">{escape(block)}</p>' for block in rest)
    footer = (
        f'<hr style="{_HR}">'
        f'<p style="{_FOOT}">{escape(_tr("reason", lang))}</p>'
        f'<p style="{_FOOT}"><a href="{escape(unsubscribe_url)}" style="{_LINK}">'
        f"{escape(_tr('unsubscribe', lang))}</a> · "
        f'<a href="{escape(manage_url)}" style="{_LINK}">{escape(_tr("manage", lang))}</a></p>'
        f'<p style="{_FOOT}">{escape(_identity_line())}</p>'
        f'<p style="{_FOOT}">{escape(_tr("disclaimer", lang))}</p>'
    )
    html = _document(
        lang, subject, f'<h1 style="{_H1}">{escape(title)}</h1>{body}{footer}'
    )
    plain = "\n".join(
        [
            text.strip(),
            "",
            "-- ",
            _tr("reason", lang),
            f"{_tr('unsubscribe', lang)}: {unsubscribe_url}",
            f"{_tr('manage', lang)}: {manage_url}",
            _identity_line(),
            _tr("disclaimer", lang),
        ]
    )
    # 服務商去重鍵：呼叫端沒給就用「收件人＋日期」（不帶 user_id 明文）
    recipient = hashlib.sha256(to.encode("utf-8")).hexdigest()[:16]
    return OutgoingEmail(
        to=to,
        subject=subject,
        html=html,
        text=plain,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        idempotency_key=idempotency_key or f"daily-brief/{recipient}/{on_date}",
    )


def confirmation_email(
    *, to: str, confirm_url: str, language: Optional[str]
) -> OutgoingEmail:
    from core.email_brief.service import CONFIRM_TTL_HOURS

    lang = _lang(language)
    subject = _tr("confirm_subject", lang)
    expiry = _tr("confirm_expiry", lang, hours=CONFIRM_TTL_HOURS)
    inner = (
        f'<h1 style="{_H1}">{escape(_tr("confirm_title", lang))}</h1>'
        f'<p style="{_P}">{escape(_tr("confirm_body", lang))}</p>'
        f'<p style="{_P}"><a href="{escape(confirm_url)}" style="{_BUTTON}">'
        f"{escape(_tr('confirm_button', lang))}</a></p>"
        f'<p style="{_FOOT}">{escape(_tr("confirm_fallback", lang))}<br>'
        f'<a href="{escape(confirm_url)}" style="{_LINK}">{escape(confirm_url)}</a></p>'
        f'<p style="{_FOOT}">{escape(expiry)}</p>'
        f'<p style="{_FOOT}">{escape(_tr("confirm_ignore", lang))}</p>'
        f'<hr style="{_HR}"><p style="{_FOOT}">{escape(_identity_line())}</p>'
    )
    plain = "\n".join(
        [
            _tr("confirm_title", lang),
            "",
            _tr("confirm_body", lang),
            "",
            confirm_url,
            "",
            expiry,
            _tr("confirm_ignore", lang),
            "",
            "-- ",
            _identity_line(),
        ]
    )
    return OutgoingEmail(
        to=to, subject=subject, html=_document(lang, subject, inner), text=plain
    )


_PAGES = {
    "confirmed": ("page_confirmed_title", "page_confirmed_body"),
    "expired": ("page_expired_title", "page_expired_body"),
    "invalid": ("page_invalid_title", "page_invalid_body"),
    "unsubscribed": ("page_unsubscribed_title", "page_unsubscribed_body"),
    "error": ("page_error_title", "page_error_body"),
}
# 連結點開先看到的一顆按鈕頁（GET 不改狀態：信箱安全掃描器會預先點開信裡每個連結）
_PROMPTS = {
    "confirm_prompt": ("confirm_title", "confirm_body", "confirm_button"),
    "unsubscribe_prompt": (
        "page_unsubscribe_prompt_title",
        "page_unsubscribe_prompt_body",
        "unsubscribe",
    ),
}


def result_page(
    outcome: str, language: Optional[str], *, action_url: Optional[str] = None
) -> str:
    """確認／退訂連結的小頁面（不需要登入、不跑 JS）。prompt 類型是一顆 POST 按鈕。"""
    lang = _lang(language)
    if outcome in _PROMPTS and action_url:
        title_key, body_key, button_key = _PROMPTS[outcome]
        title = _tr(title_key, lang)
        inner = (
            f'<h1 style="{_H1}">{escape(title)}</h1>'
            f'<p style="{_P}">{escape(_tr(body_key, lang))}</p>'
            f'<form method="post" action="{escape(action_url)}">'
            f'<button type="submit" style="{_BUTTON}border:none;cursor:pointer;font-size:15px;">'
            f"{escape(_tr(button_key, lang))}</button></form>"
        )
        return _document(lang, title, inner, noindex=True)
    title_key, body_key = _PAGES.get(outcome, _PAGES["invalid"])
    title = _tr(title_key, lang)
    inner = (
        f'<h1 style="{_H1}">{escape(title)}</h1>'
        f'<p style="{_P}">{escape(_tr(body_key, lang))}</p>'
        f'<p style="{_P}"><a href="/#settings" style="{_BUTTON}">'
        f"{escape(_tr('page_open_app', lang))}</a></p>"
    )
    return _document(lang, title, inner, noindex=True)
