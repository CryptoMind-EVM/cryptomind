"""
Universal Symbol Resolver

Resolves candidate symbols across crypto, TW stocks, and US stocks.
Uses pattern-based scoring (code formats, suffixes) without hardcoded symbol lists.
LLM NLU + external APIs (CoinGecko, yfinance, TWSE) handle symbol identification upstream.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from .tw_symbol_resolver import TWSymbolResolver

_US_PATTERN = re.compile(r"^[A-Z]{1,5}$")
_TW_CODE_PATTERN = re.compile(r"^\d{4,6}(?:\.TW|\.TWO)?$", re.IGNORECASE)

_BASE_SCORES = {
    "crypto": 0.92,
    "tw_code": 0.99,
    "tw_suffix": 0.99,
    "tw_fuzzy": 0.58,
    "us_ticker": 0.72,
}


class UniversalSymbolResolver:
    def __init__(self):
        self.tw_resolver = TWSymbolResolver()

    def _collect_market_hint_boosts(self, context_text: str) -> Dict[str, float]:
        return {"crypto": 0.0, "tw": 0.0, "us": 0.0}

    def _should_probe_tw_market(
        self, token: str, upper: str, boosts: Dict[str, float]
    ) -> bool:
        if _TW_CODE_PATTERN.match(token):
            return True
        if token.upper().endswith((".TW", ".TWO")):
            return True
        if boosts.get("tw", 0.0) > 0:
            return True
        if not _US_PATTERN.match(upper):
            return True
        return False

    def resolve_candidates(
        self, input_str: str, context_text: str = ""
    ) -> Dict[str, dict]:
        s = input_str.strip()
        upper = s.upper()
        candidates: Dict[str, dict] = {}
        boosts = self._collect_market_hint_boosts(context_text)

        tw_match = None
        if self._should_probe_tw_market(s, upper, boosts):
            tw_match = self.tw_resolver.resolve_with_metadata(s)
        if tw_match:
            match_type = tw_match.get("match_type", "fuzzy")
            base_score = {
                "code": _BASE_SCORES["tw_code"],
                "suffix": _BASE_SCORES["tw_suffix"],
                "fuzzy": _BASE_SCORES["tw_fuzzy"],
            }.get(match_type, _BASE_SCORES["tw_fuzzy"])

            if match_type == "fuzzy" and upper == s and _US_PATTERN.match(upper):
                base_score -= 0.08

            if match_type == "fuzzy":
                fuzzy_score = tw_match.get("score")
                if isinstance(fuzzy_score, (int, float)):
                    # Let stronger fuzzy matches win when context also points to TW,
                    # without hardcoding any specific symbol or market alias.
                    base_score += max(
                        0.0, min(0.15, (float(fuzzy_score) - 80.0) / 100.0)
                    )

            candidates["tw"] = {
                "symbol": tw_match["ticker"],
                "score": max(0.0, min(1.0, base_score + boosts["tw"])),
                "match_type": match_type,
            }

        is_digit = s.isdigit()
        if (
            not is_digit
            and not candidates.get("crypto")
            and s == upper
            and _US_PATTERN.match(upper)
        ):
            us_score = _BASE_SCORES["us_ticker"] + boosts["us"]
            if _TW_CODE_PATTERN.match(s):
                us_score -= 0.25
            candidates["us"] = {
                "symbol": upper,
                "score": max(0.0, min(1.0, us_score)),
                "match_type": "ticker",
            }

        return candidates

    def resolve_with_context(
        self, input_str: str, context_text: str = ""
    ) -> Dict[str, object]:
        candidates = self.resolve_candidates(input_str, context_text=context_text)
        result = {"crypto": None, "tw": None, "us": None}
        for market, payload in candidates.items():
            result[market] = payload["symbol"]

        ranked = sorted(
            ((market, payload) for market, payload in candidates.items()),
            key=lambda item: item[1]["score"],
            reverse=True,
        )

        primary_market = ranked[0][0] if ranked else None
        primary_score = ranked[0][1]["score"] if ranked else 0.0
        secondary_score = ranked[1][1]["score"] if len(ranked) > 1 else 0.0
        is_ambiguous = len(ranked) > 1 and (primary_score - secondary_score) < 0.10

        return {
            "resolution": result,
            "candidates": {
                market: {
                    "symbol": payload["symbol"],
                    "score": round(payload["score"], 4),
                    "match_type": payload["match_type"],
                }
                for market, payload in candidates.items()
            },
            "primary_market": None if is_ambiguous else primary_market,
            "primary_score": round(primary_score, 4),
            "ambiguous": is_ambiguous,
        }

    def resolve(self, input_str: str) -> Dict[str, Optional[str]]:
        """
        Backward-compatible flat resolution dict:
            {"crypto": str|None, "tw": str|None, "us": str|None}
        """
        return self.resolve_with_context(input_str)["resolution"]

    def has_matches(self, resolution: dict) -> bool:
        return any(v is not None for v in resolution.values())

    def primary_market(self, resolution: dict) -> Optional[str]:
        matches = [k for k, v in resolution.items() if v is not None]
        return matches[0] if len(matches) == 1 else None

    def matched_markets(self, resolution: dict) -> list:
        return [k for k, v in resolution.items() if v is not None]
