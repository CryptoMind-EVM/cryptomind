"""Open Collective GraphQL 客戶端（design 2026-08-17-discover-multisource §Phase 1）.

- 唯讀、匿名（公開資料免 token）；與 Manifund MCP 同為 pull 型外部來源。
- 錯誤模式對齊 manifund_client：不可用時拋 OCUnavailableError，
  路由層 fail-soft（聯邦搜尋一邊掛、另一邊照樣回）。
- schema 已於 2026-08-17 對 https://api.opencollective.com/graphql/v2 實測：
  ``accounts(searchTerm, limit, offset){ nodes { slug name type description
  website tags stats{ totalAmountReceived{ value currency } } } }``
  （舊版 ``search(term){collectives}`` 已改版不存在）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OC_GRAPHQL_URL = "https://api.opencollective.com/graphql/v2"
_TIMEOUT_SECONDS = 8.0
_TYPE_COLLECTIVE = "COLLECTIVE"

_SEARCH_QUERY = """
query($t: String!, $limit: Int!) {
  accounts(searchTerm: $t, limit: $limit, offset: 0) {
    nodes {
      slug
      name
      type
      description
      website
      tags
      stats { totalAmountReceived { value currency } }
    }
  }
}
"""


class OCUnavailableError(RuntimeError):
    """Open Collective API 不可用（網路/限流/ schema 變更）。"""


def _normalize(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OC node → Discover 正規化欄位（與 manifold 卡位對齊；url 一律官方頁）。"""
    slug = node.get("slug")
    if not slug:
        return None
    stats = node.get("stats") or {}
    received = stats.get("totalAmountReceived") or {}
    return {
        "id": slug,
        "slug": slug,
        "title": node.get("name") or slug,
        "blurb": (node.get("description") or "")[:280],
        "url": f"https://opencollective.com/{slug}",
        "website": node.get("website"),
        "tags": node.get("tags") or [],
        "total_raised": received.get("value"),
        "currency": received.get("currency"),
        "source": "oc",
        "item_type": "oc_collective",
    }


async def search_collectives(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """搜尋公開 collectives（僅 COLLECTIVE 型，排除個人帳號）。

    Raises:
        OCUnavailableError: HTTP/網路錯誤、非 2xx、或回應結構不符。
    """
    variables = {"t": query, "limit": max(1, min(int(limit), 50))}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                OC_GRAPHQL_URL,
                json={"query": _SEARCH_QUERY, "variables": variables},
                headers={"Content-Type": "application/json"},
            )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        raise OCUnavailableError(f"opencollective request failed: {exc}") from exc
    if resp.status_code == 429:
        raise OCUnavailableError("opencollective rate limited")
    if resp.status_code != 200:
        raise OCUnavailableError(f"opencollective http {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise OCUnavailableError("opencollective non-json response") from exc
    if body.get("errors"):
        # schema 變更或查詢錯誤：明確降級，不把半套資料當成功
        raise OCUnavailableError(
            f"opencollective graphql errors: {body['errors'][0].get('message', '?')}"
        )
    nodes = (((body.get("data") or {}).get("accounts") or {}).get("nodes")) or []
    results = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != _TYPE_COLLECTIVE:
            continue
        normalized = _normalize(node)
        if normalized:
            results.append(normalized)
    return results
