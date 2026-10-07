"""對宿主的通知端點送出。每 100 個 token 一批；invalidTokens 直接停用、rateLimitedTokens 記 log。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

import httpx

from . import store

logger = logging.getLogger(__name__)

TITLE_MAX = 32
BODY_MAX = 128
TARGET_URL_MAX = 1024
NOTIFICATION_ID_MAX = 128
BATCH = 100


@dataclass
class SendResult:
    successful: List[str] = field(default_factory=list)
    invalid: List[str] = field(default_factory=list)
    rate_limited: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def any_success(self) -> bool:
        return bool(self.successful)


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _batches(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _url_allowed(url: str) -> bool:
    """推播網址不可指向內網、本機或雲端 metadata（url 可能是前端 register 帶來的）。"""
    from core.tools.url_fetch import is_safe_url

    ok, reason = is_safe_url(url)
    if not ok:
        logger.warning("[miniapp] notification url blocked: %s", reason)
    return ok


def send_notification(
    targets: List[Dict[str, str]],
    *,
    notification_id: str,
    title: str,
    body: str,
    target_url: str,
    timeout: float = 10.0,
) -> SendResult:
    """``targets``＝[{url, token}]（store.tokens_for_user 的形狀）。同 url 的 token 合批。"""
    result = SendResult()
    by_url: Dict[str, List[str]] = {}
    for t in targets:
        url, token = t.get("url"), t.get("token")
        if url and token:
            by_url.setdefault(url, []).append(token)
    payload_base = {
        "notificationId": notification_id[:NOTIFICATION_ID_MAX],
        "title": clip(title, TITLE_MAX),
        "body": clip(body, BODY_MAX),
        "targetUrl": target_url[:TARGET_URL_MAX],
    }
    for url, tokens in by_url.items():
        if not _url_allowed(url):
            result.failed.extend(tokens)
            continue
        for batch in _batches(tokens, BATCH):
            try:
                resp = httpx.post(
                    url, json={**payload_base, "tokens": batch}, timeout=timeout
                )
                data = resp.json() if resp.status_code == 200 else {}
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "[miniapp] notification POST failed url=%s: %s", url, exc
                )
                result.failed.extend(batch)
                continue
            if resp.status_code != 200:
                logger.warning(
                    "[miniapp] notification POST status=%s url=%s",
                    resp.status_code,
                    url,
                )
                result.failed.extend(batch)
                continue
            ok = [t for t in (data.get("successfulTokens") or []) if t in batch]
            bad = [t for t in (data.get("invalidTokens") or []) if t in batch]
            slow = [t for t in (data.get("rateLimitedTokens") or []) if t in batch]
            result.successful.extend(ok)
            result.invalid.extend(bad)
            result.rate_limited.extend(slow)
            if bad:
                store.disable_tokens(bad, "invalid")
            if ok:
                store.mark_sent(ok)
    return result
