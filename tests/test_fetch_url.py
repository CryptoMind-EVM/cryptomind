"""Unit tests for core/tools/url_fetch.py.

Covers (per AGENTS.md test convention — success / reject / edge):
- SSRF safety filter: private IPs, loopback, cloud-metadata, bad scheme, credential URLs
- Tavily Extract backend: happy path + 401 / 429 / 4xx / empty / timeout
- Jina Reader backend: happy path + 429 / 4xx / empty / timeout
- Unified fetch_url(): with key → Tavily; without key → Jina; Tavily fail → Jina fallback
- LangChain tool wrapper fetch_url_tool: markdown output format + error formatting
"""

import json
from unittest.mock import patch

import httpx
import pytest

from core.tools import url_fetch

# ──────────────────────────────────────────────────────────────────────────────
# SSRF safety filter
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",  # AWS metadata
        "http://169.254.170.2",  # ECS task metadata
        "http://100.100.100.200",  # Alibaba metadata
        "http://metadata.google.internal",
        "http://metadata",
        "http://localhost",
        "http://localhost:8080",
        "http://foo.localhost",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://[::1]",  # IPv6 loopback
        "http://100.64.0.1",  # CGNAT
        "http://198.18.0.1",  # RFC2544 benchmark
        "ftp://example.com",  # bad scheme
        "file:///etc/passwd",  # bad scheme
        "",  # empty
        "https://example.com/?api_key=secret",  # credential in query
        "https://example.com/?token=abc123",  # credential in query
        "https://example.com/?access_token=xyz",  # credential in query
        "https://example.com/?signature=foo",  # credential in query
        # ── 下列為本輪審核補強：IPv4-mapped IPv6 / 編碼變體 / 未指定 ──
        "http://0.0.0.0",  # unspecified — 不該能連
        "http://[::]",  # IPv6 unspecified
        "http://[::ffff:127.0.0.1]",  # IPv4-mapped IPv6 loopback（繞過天真 的 loopback 檢查）
        "http://[::ffff:169.254.169.254]",  # IPv4-mapped IPv6 metadata
        "http://[::ffff:10.0.0.1]",  # IPv4-mapped IPv6 private
        "http://2130706433",  # 十進位編碼的 127.0.0.1（== 2130706433）
        "http://0x7f000001",  # hex 編碼的 127.0.0.1
        "http://0177.0.0.1",  # 八進位編碼的 127.0.0.1（0177 == 127）
        "http://127.1",  # 簡寫 loopback（少見但 Python ipaddress 不接受，應 fail-closed）
    ],
)
def test_is_safe_url_rejects_dangerous(url):
    ok, reason = url_fetch.is_safe_url(url)
    assert not ok, f"expected reject for {url!r}, got ok=True"
    assert reason, f"expected reason for {url!r}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "http://example.com",  # http still allowed (not just https)
        "https://example.com/path?utm_source=feed",  # benign query ok
        "https://example.com/path?q=hello+world",
        "https://8.8.8.8/",  # public IP is fine
        "https://1.1.1.1/dns-query",
    ],
)
def test_is_safe_url_accepts_safe(url):
    """Safe URLs should pass — example.com / public IPs / benign query params.

    Note: hostname-based URLs (like example.com) need DNS to resolve; we mock
    getaddrinfo to return a public IP so the test is network-independent.
    """
    # Mock DNS for any hostname (IP-only URLs short-circuit before DNS).
    parsed = url.split("://", 1)[1].split("/", 1)[0]
    if not all(c.isdigit() or c == "." or c == "[" or c == "]" or c == ":" for c in parsed):
        # it's a hostname → mock DNS to return a public IP
        with patch(
            "socket.getaddrinfo",
            return_value=[
                (
                    0,
                    0,
                    0,
                    "",
                    ("93.184.216.34", 0),
                )
            ],
        ):
            ok, reason = url_fetch.is_safe_url(url)
    else:
        ok, reason = url_fetch.is_safe_url(url)
    assert ok, f"expected accept for {url!r}, got reject: {reason}"


@pytest.mark.unit
def test_is_safe_url_fail_closed_on_dns_failure():
    """DNS resolution failure → blocked (fail-closed)."""
    with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
        ok, reason = url_fetch.is_safe_url("https://nonexistent.invalid/")
    assert not ok
    assert "fail-closed" in reason or "DNS" in reason


# ──────────────────────────────────────────────────────────────────────────────
# B. Hostname policy blocklist (env-driven) — 2026-07-23 新增
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_policy_blocklist_blocks_exact_domain(monkeypatch):
    """env 設 corp.example.com → 精確 domain 被擋。"""
    monkeypatch.setenv("FETCH_URL_BLOCKED_HOSTNAMES", "corp.example.com")
    ok, reason = url_fetch.is_safe_url("https://corp.example.com/secret")
    assert not ok
    assert "policy blocklist" in reason


@pytest.mark.unit
def test_policy_blocklist_blocks_subdomain(monkeypatch):
    """env 設 corp.example.com → 子網域 sub.corp.example.com 也被擋。"""
    monkeypatch.setenv("FETCH_URL_BLOCKED_HOSTNAMES", "corp.example.com")
    ok, reason = url_fetch.is_safe_url("https://sub.corp.example.com/secret")
    assert not ok
    assert "policy blocklist" in reason


@pytest.mark.unit
def test_policy_blocklist_does_not_block_parent_domain(monkeypatch):
    """env 設 corp.example.com → parent domain example.com 不誤殺。"""
    monkeypatch.setenv("FETCH_URL_BLOCKED_HOSTNAMES", "corp.example.com")
    with patch(
        "socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
    ):
        ok, reason = url_fetch.is_safe_url("https://example.com/")
    assert ok, f"parent domain 不該被誤擋: {reason}"


@pytest.mark.unit
def test_policy_blocklist_does_not_block_unrelated_substring(monkeypatch):
    """env 設 corp.example.com → notcorp.example.com 不命中（字首不符）。"""
    monkeypatch.setenv("FETCH_URL_BLOCKED_HOSTNAMES", "corp.example.com")
    with patch(
        "socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
    ):
        ok, _ = url_fetch.is_safe_url("https://notcorp.example.com/")
    assert ok, "notcorp.example.com 不該被字首誤殺"


@pytest.mark.unit
def test_policy_blocklist_empty_env_no_effect(monkeypatch):
    """env 未設 / 空 → 不影響現有行為（example.com 正常通過）。"""
    monkeypatch.delenv("FETCH_URL_BLOCKED_HOSTNAMES", raising=False)
    with patch(
        "socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
    ):
        ok, _ = url_fetch.is_safe_url("https://example.com/")
    assert ok


@pytest.mark.unit
def test_policy_blocklist_multiple_entries(monkeypatch):
    """多個 domain（逗號分隔 + 帶空白）都生效。"""
    monkeypatch.setenv(
        "FETCH_URL_BLOCKED_HOSTNAMES", " corp.example.com , internal.corp ,"
    )
    ok1, _ = url_fetch.is_safe_url("https://corp.example.com/")
    ok2, _ = url_fetch.is_safe_url("https://internal.corp/")
    assert not ok1
    assert not ok2


# ──────────────────────────────────────────────────────────────────────────────
# Tavily Extract backend
# ──────────────────────────────────────────────────────────────────────────────


def _fake_resp(status_code=200, json_body=None, text="", url="https://example.com/a"):
    """Build a fake httpx.Response."""
    r = httpx.Response(
        status_code=status_code,
        text=text or (json.dumps(json_body) if json_body else ""),
        request=httpx.Request("GET", url),
    )
    return r


class _FakeStreamCM:
    """Fake context manager mimicking ``with httpx.stream(...) as resp:``.

    Jina 改用 httpx.stream 串流讀取（見 #4 memory-DoS 修復）後，舊的
    ``patch(httpx.get, return_value=fake_resp)`` 不再適用；改用本類別模擬
    串流回應：支援 ``status_code``、``iter_bytes()``、``url``，並可作為
    ``with`` context manager。
    """

    def __init__(self, status_code=200, text="", url="https://example.com/a", chunk_size=8192):
        self.status_code = status_code
        self._text = text
        self.url = httpx.URL(url)
        # 把 text 編碼成 bytes，切成 chunk_size 的 chunk 模擬 iter_bytes
        raw = text.encode("utf-8")
        self._chunks = [
            raw[i : i + chunk_size] for i in range(0, len(raw), chunk_size)
        ] or [b""]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size=8192):
        for c in self._chunks:
            yield c


def _patch_jina_stream(fake_cm):
    """Patch ``httpx.stream`` to return ``fake_cm`` (a context manager)."""
    return patch("core.tools.url_fetch.httpx.stream", return_value=fake_cm)


@pytest.mark.unit
def test_fetch_with_tavily_happy_path():
    fake = _fake_resp(
        200,
        json_body={
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "Hello",
                    "raw_content": "# Hello\n\nWorld",
                }
            ],
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("https://example.com/a", "key-123")
    assert result["success"] is True
    assert result["title"] == "Hello"
    assert "World" in result["content"]
    assert result["backend"] == "tavily"
    assert result["truncated"] is False


@pytest.mark.unit
def test_fetch_with_tavily_truncates_long_content():
    long_content = "x" * (url_fetch._DEFAULT_MAX_CHARS + 5000)
    fake = _fake_resp(
        200,
        json_body={
            "results": [
                {"url": "https://example.com/long", "raw_content": long_content}
            ]
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("https://example.com/long", "key")
    assert result["success"] is True
    assert result["truncated"] is True
    assert len(result["content"]) == url_fetch._DEFAULT_MAX_CHARS


@pytest.mark.unit
def test_fetch_with_tavily_final_url_private_ip_blocked():
    """D. Tavily 回報的 final_url 落在 private IP → fail-closed，不回 content。"""
    fake = _fake_resp(
        200,
        json_body={
            "results": [
                {
                    "url": "http://10.0.0.5/internal",  # final_url 是內網
                    "title": "Leaked",
                    "raw_content": "super secret internal data",
                }
            ]
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("https://example.com/a", "key")
    assert result["success"] is False
    assert "unsafe" in result["error"].lower()
    # 不可回傳可能含內網資料的 content
    assert "content" not in result


@pytest.mark.unit
def test_fetch_with_tavily_final_url_metadata_blocked():
    """D. final_url 指向雲 metadata → 同樣 fail-closed。"""
    fake = _fake_resp(
        200,
        json_body={
            "results": [
                {
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "title": "Creds",
                    "raw_content": "iam role arn",
                }
            ]
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("https://example.com/a", "key")
    assert result["success"] is False
    assert "content" not in result


@pytest.mark.unit
def test_fetch_with_tavily_final_url_public_domain_passes():
    """D. final_url 是正常 public domain → 正常回傳（不誤擋）。"""
    fake = _fake_resp(
        200,
        json_body={
            "results": [
                {
                    "url": "https://example.com/final",  # public domain
                    "title": "OK",
                    "raw_content": "public content",
                }
            ]
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("https://example.com/a", "key")
    assert result["success"] is True
    assert result["content"] == "public content"
    assert result["final_url"] == "https://example.com/final"


@pytest.mark.unit
def test_fetch_with_tavily_401_returns_structured_error():
    fake = _fake_resp(401)
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("u", "bad-key")
    assert result["success"] is False
    assert "401" in result["error"]


@pytest.mark.unit
def test_fetch_with_tavily_429_returns_structured_error():
    fake = _fake_resp(429)
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("u", "key")
    assert result["success"] is False
    assert "429" in result["error"]


@pytest.mark.unit
def test_fetch_with_tavily_empty_results_with_failed_results():
    fake = _fake_resp(
        200,
        json_body={
            "results": [],
            "failed_results": [{"url": "u", "error": "page not found"}],
        },
    )
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("u", "key")
    assert result["success"] is False
    assert "page not found" in result["error"]


@pytest.mark.unit
def test_fetch_with_tavily_empty_raw_content():
    fake = _fake_resp(200, json_body={"results": [{"url": "u", "raw_content": ""}]})
    with patch("core.tools.url_fetch.httpx.post", return_value=fake):
        result = url_fetch.fetch_with_tavily("u", "key")
    assert result["success"] is False
    assert "空" in result["error"] or "empty" in result["error"].lower()


@pytest.mark.unit
def test_fetch_with_tavily_timeout():
    with patch(
        "core.tools.url_fetch.httpx.post",
        side_effect=httpx.TimeoutException("slow"),
    ):
        result = url_fetch.fetch_with_tavily("u", "key")
    assert result["success"] is False
    assert "timeout" in result["error"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# Jina Reader backend
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_with_jina_happy_path():
    body = "Title: Hello World\n\nURL Source: https://example.com/a\n\nMarkdown Content:\n# Hello\n\nFull article body here."
    fake = _FakeStreamCM(200, text=body)
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is True
    assert result["title"] == "Hello World"
    assert "Full article body" in result["content"]
    assert result["backend"] == "jina"


@pytest.mark.unit
def test_fetch_with_jina_429_returns_rate_limit_message():
    fake = _FakeStreamCM(429)
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is False
    assert "429" in result["error"]
    assert "Jina" in result["error"] or "額度" in result["error"]


@pytest.mark.unit
def test_fetch_with_jina_empty_response():
    fake = _FakeStreamCM(200, text="   ")
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is False
    assert "空" in result["error"] or "empty" in result["error"].lower()


@pytest.mark.unit
def test_fetch_with_jina_truncates_long_markdown():
    body = "Title: T\n\nMarkdown Content:\n" + ("y" * (url_fetch._DEFAULT_MAX_CHARS + 1000))
    fake = _FakeStreamCM(200, text=body)
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is True
    assert result["truncated"] is True
    assert len(result["content"]) == url_fetch._DEFAULT_MAX_CHARS


@pytest.mark.unit
def test_fetch_with_jina_timeout():
    # httpx.stream 本身（建立連線階段）timeout
    with patch(
        "core.tools.url_fetch.httpx.stream",
        side_effect=httpx.TimeoutException("slow"),
    ):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is False
    assert "timeout" in result["error"].lower()


@pytest.mark.unit
def test_fetch_with_jina_truncates_at_byte_limit():
    """#4 memory-DoS 防護：回應位元組超過 _MAX_DOWNLOAD_BYTES 時，串流讀取到上限就停，
    並標 truncated=True（不把幾百 MB 全載入記憶體）。"""
    # 製造一個超過 byte 上限的 body；把 _MAX_DOWNLOAD_BYTES 調小讓測試快
    huge = "x" * (url_fetch._MAX_DOWNLOAD_BYTES + 1024)
    fake = _FakeStreamCM(200, text=huge)
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is True
    assert result["truncated"] is True
    # 實際讀進來的 bytes 不該超過上限（decode 後字元數 ≤ 上限）
    assert len(result["content"].encode("utf-8")) <= url_fetch._MAX_DOWNLOAD_BYTES


@pytest.mark.unit
def test_fetch_with_jina_blocks_unsafe_source_url():
    """#3 縱深防護：Jina 回應的 'URL Source' 落在 private/internal IP → fail-closed，
    不回傳可能含內網資料的 content（與 Tavily final_url 檢查對稱）。
    模擬 Jina server 被目標站 open-redirect 騙去抓 10.0.0.5/internal 的情境。
    """
    body = (
        "Title: Leaked\n\n"
        "URL Source: http://10.0.0.5/internal\n\n"
        "Markdown Content:\nsuper secret internal data"
    )
    fake = _FakeStreamCM(200, text=body)
    with _patch_jina_stream(fake):
        result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is False
    assert "unsafe" in result["error"].lower()
    # 不可回傳可能含內網資料的 content
    assert "content" not in result


@pytest.mark.unit
def test_fetch_with_jina_accepts_safe_source_url():
    """#3 對照：安全的 source URL 應正常通過（確認檢查不誤殺）。"""
    body = (
        "Title: Hello\n\n"
        "URL Source: https://example.com/a\n\n"
        "Markdown Content:\n# Hello\n\nbody"
    )
    # is_safe_url 對 example.com 會 DNS 查詢；mock 成公網 IP
    with patch(
        "socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
    ):
        fake = _FakeStreamCM(200, text=body)
        with _patch_jina_stream(fake):
            result = url_fetch.fetch_with_jina("https://example.com/a")
    assert result["success"] is True
    assert "Hello" in result["content"]


# ──────────────────────────────────────────────────────────────────────────────
# Unified fetch_url() routing
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_url_blocks_ssrf_before_any_network_call():
    """SSRF rejection must happen BEFORE key resolution or any HTTP call."""
    with (
        patch("core.tools.url_fetch.httpx.post") as mock_post,
        patch("core.tools.url_fetch.httpx.get") as mock_get,
        patch("core.tools.url_fetch.httpx.stream") as mock_stream,
    ):
        result = url_fetch.fetch_url("http://169.254.169.254")
    assert result["success"] is False
    # 訊息已英文化（#352）——斷言英文文案，避免與翻譯耦合。
    assert "unsafe" in result["error"].lower()
    mock_post.assert_not_called()
    mock_get.assert_not_called()
    mock_stream.assert_not_called()


@pytest.mark.unit
def test_fetch_url_uses_tavily_when_key_present():
    with (
        patch(
            "core.tools.key_resolver.resolve_tool_key",
            return_value="tav-key",
        ),
        patch.object(
            url_fetch,
            "fetch_with_tavily",
            return_value={"success": True, "content": "from tavily", "title": "T"},
        ) as mock_t,
        patch.object(url_fetch, "fetch_with_jina") as mock_j,
    ):
        result = url_fetch.fetch_url("https://example.com/a")
    assert result["success"] is True
    assert "from tavily" in result["content"]
    mock_t.assert_called_once()
    mock_j.assert_not_called()


@pytest.mark.unit
def test_fetch_url_falls_back_to_jina_when_no_key():
    with (
        patch("core.tools.key_resolver.resolve_tool_key", return_value=None),
        patch.object(url_fetch, "fetch_with_tavily") as mock_t,
        patch.object(
            url_fetch,
            "fetch_with_jina",
            return_value={"success": True, "content": "from jina", "title": "J"},
        ) as mock_j,
    ):
        result = url_fetch.fetch_url("https://example.com/a")
    assert result["success"] is True
    assert "from jina" in result["content"]
    mock_t.assert_not_called()
    mock_j.assert_called_once()


@pytest.mark.unit
def test_fetch_url_tavily_failure_falls_back_to_jina():
    """Tavily returning an error should fall through to Jina (mirrors web_search → DDG)."""
    with (
        patch("core.tools.key_resolver.resolve_tool_key", return_value="tav-key"),
        patch.object(
            url_fetch,
            "fetch_with_tavily",
            return_value={"success": False, "error": "Tavily 429"},
        ),
        patch.object(
            url_fetch,
            "fetch_with_jina",
            return_value={"success": True, "content": "from jina"},
        ) as mock_j,
    ):
        result = url_fetch.fetch_url("https://example.com/a")
    assert result["success"] is True
    mock_j.assert_called_once()


@pytest.mark.unit
def test_fetch_url_all_backends_fail_returns_error():
    with (
        patch("core.tools.key_resolver.resolve_tool_key", return_value=None),
        patch.object(
            url_fetch,
            "fetch_with_jina",
            return_value={"success": False, "error": "Jina 429"},
        ),
    ):
        result = url_fetch.fetch_url("https://example.com/a")
    assert result["success"] is False
    assert "429" in result["error"]


# ──────────────────────────────────────────────────────────────────────────────
# LangChain tool wrapper
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_url_tool_returns_markdown_on_success():
    with patch.object(
        url_fetch,
        "fetch_url",
        return_value={
            "success": True,
            "title": "Hello",
            "content": "# Hello\nWorld",
            "final_url": "https://example.com/a",
            "backend": "jina",
            "truncated": False,
        },
    ):
        out = url_fetch.fetch_url_tool.invoke({"url": "https://example.com/a"})
    assert "Fetched Content" in out
    assert "Hello" in out
    assert "via jina" in out
    assert "World" in out
    # C. 不可信內容邊界標記
    assert "UNTRUSTED EXTERNAL CONTENT" in out
    assert "--- BEGIN UNTRUSTED EXTERNAL CONTENT ---" in out
    assert "--- END UNTRUSTED EXTERNAL CONTENT ---" in out


@pytest.mark.unit
def test_fetch_url_tool_returns_error_string_on_failure():
    with patch.object(
        url_fetch,
        "fetch_url",
        return_value={"success": False, "error": "blocked: SSRF"},
    ):
        out = url_fetch.fetch_url_tool.invoke({"url": "http://169.254.169.254"})
    assert "Failed to fetch" in out
    assert "SSRF" in out


@pytest.mark.unit
def test_fetch_url_tool_shows_truncation_warning():
    with patch.object(
        url_fetch,
        "fetch_url",
        return_value={
            "success": True,
            "title": "T",
            "content": "x" * 100,
            "final_url": "u",
            "backend": "tavily",
            "truncated": True,
        },
    ):
        out = url_fetch.fetch_url_tool.invoke({"url": "https://example.com/long"})
    assert "truncated" in out.lower() or "截斷" in out
