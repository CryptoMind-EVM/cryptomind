"""AI 回答快照分享的純函式（core/answer_share.py，2026-10-05 任務 D）：token、遮蔽、摘要、OG meta。"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.unit

from core import answer_share as a  # noqa: E402

EVM = "0x" + "ab12" * 10  # 42 字元
TX = "0x" + "cd" * 32  # 66 字元
SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # 44 字元 base58
BTC = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
BECH = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"


class TestToken:
    def test_shape_and_uniqueness(self):
        tokens = {a.new_token() for _ in range(200)}
        assert len(tokens) == 200
        for t in tokens:
            assert re.fullmatch(r"[A-Za-z0-9_-]{32}", t)
            assert a.valid_token_shape(t)

    @pytest.mark.parametrize(
        "bad",
        ["", "short", "x" * 31, "x" * 33, "../etc/passwd" + "x" * 20, "a b" * 11, None],
    )
    def test_malformed_tokens_are_rejected_before_any_lookup(self, bad):
        assert a.valid_token_shape(bad) is False

    def test_hash_is_stable_and_not_the_token(self):
        t = a.new_token()
        h = a.hash_token(t)
        assert h == a.hash_token(t)
        assert h != t and len(h) == 64 and t not in h
        assert a.hash_token(a.new_token()) != h


class TestRedact:
    @pytest.mark.parametrize(
        "secret, tag",
        [
            (EVM, "[address]"),
            (SOL, "[address]"),
            (BTC, "[address]"),
            (BECH, "[address]"),
            (TX, "[hash]"),
            ("a1b2" * 16, "[hash]"),
        ],
    )
    def test_addresses_and_hashes_are_masked(self, secret, tag):
        out, n = a.redact(f"我的錢包 {secret} 剛轉出")
        assert secret not in out and tag in out and n == 1

    def test_email_is_masked(self):
        out, n = a.redact("寄給 someone.name+tag@example.co.uk 看看")
        assert "@" not in out and "[email]" in out and n == 1

    def test_counts_every_hit(self):
        out, n = a.redact(f"{EVM} 和 {SOL} 以及 me@x.io")
        assert n == 3 and EVM not in out and SOL not in out

    def test_normal_analysis_text_is_untouched(self):
        text = "BTC 目前 6.8 萬附近整理，24h 成交量低於 30 日均量。ETH/BTC=0.052，RSI 58。https://example.com/a?b=1"
        assert a.redact(text) == (text, 0)

    def test_long_ordinary_words_are_not_masked(self):
        text = "internationalization and Pneumonoultramicroscopicsilicovolcanoconiosis are long"
        assert a.redact(text)[1] == 0

    def test_empty(self):
        assert a.redact("") == ("", 0)
        assert a.redact(None) == ("", 0)


class TestSnapshot:
    def test_cleans_question_and_limits_lengths(self):
        s = a.prepare_snapshot("  BTC\n\t現在  怎麼看\u0000 ", "答案")
        assert s["question"] == "BTC 現在 怎麼看"
        long = a.prepare_snapshot("q" * 900, "x" * (a.ANSWER_MAX + 500))
        assert len(long["question"]) == a.QUESTION_MAX
        assert len(long["answer"]) <= a.ANSWER_MAX + 1 and long["truncated"] is True
        assert a.prepare_snapshot("q", "短")["truncated"] is False

    def test_redaction_applies_to_both_sides_and_is_deterministic(self):
        s1 = a.prepare_snapshot(f"錢包 {EVM} 怎麼看", f"地址 {EVM} 持有 BTC")
        assert EVM not in s1["question"] + s1["answer"]
        assert s1["redactions"] == 2
        assert (
            a.prepare_snapshot(f"錢包 {EVM} 怎麼看", f"地址 {EVM} 持有 BTC") == s1
        )  # 預覽與建立要得到同一份

    def test_empty_question_or_answer_is_rejected(self):
        assert a.prepare_snapshot("", "x") is None
        assert a.prepare_snapshot("q", "   ") is None


class TestSnippet:
    def test_plain_text_first_150(self):
        md = (
            "## 結論\n**BTC** 整理中\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- 要點一\n- [連結](https://x.y) 文字\n```\ncode\n```\n"
            + "字" * 400
        )
        s = a.og_snippet(md)
        assert len(s) <= a.OG_DESCRIPTION_LEN + 1
        for ch in ("#", "**", "|", "```", "](", "- "):
            assert ch not in s
        assert s.startswith("結論 BTC 整理中")

    def test_short_text_has_no_ellipsis(self):
        assert a.og_snippet("簡短答案") == "簡短答案"


class TestMeta:
    PAGE = "<head><title>x</title><!--OG--></head><body></body>"

    def test_escaped_and_noindex(self):
        out = a.apply_share_meta(
            self.PAGE, '"><script>alert(1)</script>', "<b>desc</b> & more"
        )
        assert "<script>" not in out and "<b>" not in out
        assert "&lt;script&gt;" in out and "&amp; more" in out
        assert 'name="robots" content="noindex"' in out
        assert 'property="og:title"' in out and 'property="og:description"' in out
        assert 'name="twitter:title"' in out

    def test_title_is_the_question(self):
        out = a.apply_share_meta(self.PAGE, "BTC 現在怎麼看", "desc")
        assert "BTC 現在怎麼看" in re.search(r'og:title" content="([^"]*)"', out).group(
            1
        )

    def test_not_found_variant_is_noindex_and_generic(self):
        out = a.apply_share_meta(self.PAGE, None, None)
        assert 'name="robots" content="noindex"' in out and "og:title" not in out


class TestRedactCoverage:
    """資安審查補的缺口：這些格式也要遮蔽。"""

    @pytest.mark.parametrize(
        "secret",
        [
            "0X" + "ab12" * 10,  # 大寫 X
            "G"
            + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
            * 2,  # Stellar／Pi（G＋55 個 base32）去掉多的在下面截
            "EQ" + "A" * 46,  # TON
            "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",  # testnet bech32
            "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",  # WIF 私鑰（51）
            "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn",  # WIF 壓縮（52）
            "xprv"
            + "9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi",
            "sk-proj-" + "a1B2c3D4" * 4,  # API key
            "ab12" * 10,  # 無前綴 40 hex
            "0x" + "ef" * 65,  # 130 hex 簽章
        ],
    )
    def test_extra_secret_formats_are_masked(self, secret):
        if secret.startswith("G") and len(secret) != 56:
            secret = secret[:56]
        out, n = a.redact(f"內容 {secret} 結束")
        assert secret not in out and n >= 1, secret

    def test_invisible_characters_cannot_split_an_address(self):
        zw = "\u200b"
        split = EVM[:20] + zw + EVM[20:]
        out = a.prepare_snapshot("q", f"地址 {split} 結束")["answer"]
        assert EVM not in out and EVM[:20] not in out

    def test_bidi_and_zero_width_are_stripped_from_the_question(self):
        assert a.clean_question("BTC\u202e 怎麼\u200b看") == "BTC 怎麼看"

    def test_address_straddling_the_question_length_limit_is_still_masked(self):
        q = "x" * (a.QUESTION_MAX - 10) + " " + EVM
        out = a.prepare_snapshot(q, "答案")["question"]
        assert EVM[:12] not in out, "先遮蔽、再截斷：不能留下位址的前半段"

    def test_address_straddling_the_answer_limit_is_still_masked(self):
        ans = "x" * (a.ANSWER_MAX - 10) + " " + EVM
        out = a.prepare_snapshot("q", ans)["answer"]
        assert EVM[:12] not in out

    def test_mnemonic_and_private_key_in_the_question_are_scrubbed(self):
        phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        s = a.prepare_snapshot(f"我的助記詞是 {phrase} 怎麼辦", "答案")
        assert "abandon ability" not in s["question"] and s["redactions"] >= 1


class TestPerformance:
    def test_long_unbroken_runs_do_not_blow_up(self):
        import time

        text = "a" * 200_000
        t0 = time.perf_counter()
        a.prepare_snapshot("q", text)
        a.redact("b" * 100_000)
        assert time.perf_counter() - t0 < 1.5, "email／位址 regex 不能是 O(n²)"


class TestOgDefang:
    def test_urls_never_appear_in_the_preview_card(self):
        snip = a.og_snippet(
            "快去 https://evil.example/login?x=1 領獎 www.phish.io/a 或 http://a.b"
        )
        assert "http" not in snip and "www." not in snip and "[link]" in snip

    def test_title_defangs_urls_too(self):
        out = a.apply_share_meta("<!--OG-->", "看 https://evil.example 這個", "d")
        assert "evil.example" not in out and "[link]" in out
