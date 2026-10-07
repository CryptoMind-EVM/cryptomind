"""MCP content 解包測試（真實 Manifund 回應結構，2026-08-15 實測）。"""

from __future__ import annotations

from core.tools.manifund_client import _unwrap_content


class TestUnwrapContent:
    def test_text_block_json_unwrapped(self):
        payload = {"projects": [{"slug": "a"}]}
        block = [{"type": "text", "text": '{"projects": [{"slug": "a"}]}'}]
        assert _unwrap_content(block) == payload

    def test_text_block_plain_text_returned_as_string(self):
        assert _unwrap_content([{"type": "text", "text": "plain"}]) == "plain"

    def test_list_of_non_blocks_passthrough(self):
        data = [{"slug": "a"}, {"slug": "b"}]
        assert _unwrap_content(data) is data

    def test_dict_passthrough(self):
        data = {"search_mode": "semantic", "projects": []}
        assert _unwrap_content(data) is data

    def test_empty_list_passthrough(self):
        assert _unwrap_content([]) == []


class TestExtractItems:
    def test_search_envelope(self):
        from api.routers.discover import _extract_items

        payload = {"search_mode": "semantic", "projects": [{"slug": "a"}]}
        assert _extract_items(payload, "projects", "results") == [{"slug": "a"}]

    def test_comments_envelope(self):
        from api.routers.discover import _extract_items

        payload = {"comments": [{"body": "hi"}]}
        assert _extract_items(payload, "comments") == [{"body": "hi"}]

    def test_plain_list_passthrough(self):
        from api.routers.discover import _extract_items

        data = [1, 2]
        assert _extract_items(data, "projects") is data

    def test_no_matching_key_returns_payload(self):
        from api.routers.discover import _extract_items

        payload = {"other": 1}
        assert _extract_items(payload, "projects") == payload
