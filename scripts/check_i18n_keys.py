#!/usr/bin/env python3
"""Enforce i18n key alignment across all locale JSON files.

Returns exit code 1 if any locale is missing keys that exist in the reference
locale (en.json by default). Designed as a CI gate.

Usage:
    python scripts/check_i18n_keys.py
    REF_LOCALE=zh-TW python scripts/check_i18n_keys.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "web" / "js" / "i18n"
REF_LOCALE = os.environ.get("REF_LOCALE", "en")


def flatten_keys(obj: object, prefix: str = "") -> set[str]:
    """Recursively collect all leaf keys using dot notation."""
    keys: set[str] = set()
    if not isinstance(obj, dict):
        return keys
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= flatten_keys(v, full)
        else:
            keys.add(full)
    return keys


def load_locale(path: Path) -> set[str]:
    """Load a locale JSON file and return its flattened key set."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return flatten_keys(data)
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {path.name}: {e}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    locales = sorted(I18N_DIR.glob("*.json"))
    if not locales:
        print("ERROR: no locale files found in web/js/i18n/", file=sys.stderr)
        return 2

    ref_path = I18N_DIR / f"{REF_LOCALE}.json"
    if not ref_path.exists():
        print(
            f"ERROR: reference locale {REF_LOCALE}.json not found",
            file=sys.stderr,
        )
        return 2

    ref_keys = load_locale(ref_path)
    print(f"reference_locale={REF_LOCALE}")
    print(f"reference_keys={len(ref_keys)}")
    print()

    total_missing = 0
    failed = False

    for locale_path in locales:
        if locale_path == ref_path:
            continue
        locale_name = locale_path.stem
        locale_keys = load_locale(locale_path)

        missing = ref_keys - locale_keys
        extra = locale_keys - ref_keys

        if not missing and not extra:
            print(f"  {locale_name}: OK ({len(locale_keys)} keys)")
            continue

        failed = True
        if missing:
            total_missing += len(missing)
            print(
                f"  {locale_name}: MISSING {len(missing)} keys "
                f"(present in {REF_LOCALE} but not in {locale_name})"
            )
            for k in sorted(missing)[:20]:
                print(f"    - {k}")
            if len(missing) > 20:
                print(f"    ... and {len(missing) - 20} more")
        if extra:
            print(
                f"  {locale_name}: EXTRA {len(extra)} keys "
                f"(present in {locale_name} but not in {REF_LOCALE})"
            )
            for k in sorted(extra)[:20]:
                print(f"    + {k}")
            if len(extra) > 20:
                print(f"    ... and {len(extra) - 20} more")

    print()
    print(f"total_missing_keys={total_missing}")

    if failed:
        print("FAIL: locale key alignment broken")
        return 1
    print("OK: all locale keys aligned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
