"""
Regression test: ensure no Pi Network remnants exist in frontend files.

The project migrated from Pi Network to TON Connect. This test prevents
Pi-specific references (SDK, branding, wallet names, API endpoints) from
reappearing in any frontend file.

If a legitimate reference is needed (e.g., a historical changelog), add it
to the ALLOWED_PATTERNS whitelist below with a justification.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

# Patterns that indicate a Pi Network remnant.
# Uses word-boundary-aware patterns to avoid matching "API" (which contains "pi").
PI_PATTERNS = [
    re.compile(r"Pi\s+Network", re.IGNORECASE),
    re.compile(r"Pi\s+Browser", re.IGNORECASE),
    re.compile(r"PiBrowser\b", re.IGNORECASE),  # camelCase variant (isPiBrowser)
    re.compile(r"canMakePiPayment\b", re.IGNORECASE),  # camelCase variant
    re.compile(r"Pi\s+SDK", re.IGNORECASE),
    re.compile(r"minepi", re.IGNORECASE),
    re.compile(r"sdk\.minepi", re.IGNORECASE),
    re.compile(r"socialchain\.app", re.IGNORECASE),
    re.compile(r"Pi\s+Wallet", re.IGNORECASE),
    re.compile(r"pi-auth\b", re.IGNORECASE),
    re.compile(r"pi_uid\b", re.IGNORECASE),
    re.compile(r"pi-login", re.IGNORECASE),
    re.compile(r"initPiSDK", re.IGNORECASE),
    re.compile(r"Pi\.createPayment", re.IGNORECASE),
    re.compile(r"Pi\.authenticate", re.IGNORECASE),
    re.compile(r"Pi\.init\b", re.IGNORECASE),
]

# Files/patterns that are allowed to contain these strings (with justification).
# Each entry is a substring of the file path.
ALLOWED_FILES: list[str] = [
    # ton-auth.js may reference the old pi-auth.js it replaced (documentation).
    "ton-auth.js",
]

# File extensions to scan
SCAN_EXTENSIONS = {".html", ".js", ".css", ".json"}


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_number, matched_pattern, line_text) for Pi remnants."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern in PI_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, pattern.pattern, line.strip()[:120]))
    return hits


def test_no_pi_network_remnants_in_frontend():
    """Scan all frontend files for Pi Network references."""
    violations: list[str] = []

    for path in WEB.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        # Skip node_modules, dist, etc.
        if any(part in {"node_modules", "dist", ".vite"} for part in path.parts):
            continue

        # Check whitelist
        rel = str(path.relative_to(ROOT))
        if any(allowed in rel for allowed in ALLOWED_FILES):
            continue

        hits = _scan_file(path)
        for lineno, pattern_name, line_text in hits:
            violations.append(f"  {rel}:{lineno}  [{pattern_name}]\n    {line_text}")

    assert not violations, (
        f"Found {len(violations)} Pi Network remnant(s) in frontend files.\n"
        "The project migrated to TON Connect — no Pi references should remain.\n"
        "If a reference is legitimate, add the file to ALLOWED_FILES in "
        f"{__file__}\n\n" + "\n".join(violations)
    )
