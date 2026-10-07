from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuardDecision(str, Enum):
    """A policy result, never an investment recommendation or execution command."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True)
class GuardEvaluation:
    decision: GuardDecision
    reason_codes: tuple[str, ...]
    policy_matched: bool
