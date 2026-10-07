#!/usr/bin/env python3
"""Merge temporary _new_keys_*.json files into the canonical locale JSONs.

After subagents extract hardcoded Chinese into per-namespace temp files, this
script merges them into web/js/i18n/en.json and web/js/i18n/zh-TW.json.

Temp file format:
    {
      "namespace": {
        "keyName": {"en": "English", "zh-TW": "繁體中文"}
      }
    }

Usage:
    python scripts/merge_i18n_keys.py           # merge + verify
    python scripts/merge_i18n_keys.py --dry-run # preview only
    python scripts/merge_i18n_keys.py --clean   # remove temp files after merge
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "web" / "js" / "i18n"
SCRIPTS_DIR = ROOT / "scripts"


def deep_merge(target: dict, source: dict, path: str = "") -> tuple[dict, list[str]]:
    """Deep-merge source into target. Returns (merged, conflicts).

    Conflicts = keys where both target and source have a non-dict value
    that differs.
    """
    conflicts: list[str] = []
    for k, src_val in source.items():
        full_path = f"{path}.{k}" if path else k
        if k not in target:
            target[k] = src_val
            continue
        tgt_val = target[k]
        if isinstance(tgt_val, dict) and isinstance(src_val, dict):
            _, sub_conflicts = deep_merge(tgt_val, src_val, full_path)
            conflicts.extend(sub_conflicts)
        elif tgt_val == src_val:
            continue
        else:
            conflicts.append(f"{full_path}: target={tgt_val!r} source={src_val!r}")
    return target, conflicts


def load_temp_files() -> list[tuple[Path, dict]]:
    """Load all scripts/_new_keys_*.json files."""
    temps = []
    for path in sorted(SCRIPTS_DIR.glob("_new_keys_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            temps.append((path, data))
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON in {path.name}: {e}", file=sys.stderr)
    return temps


def build_locale_map(temps: list[tuple[Path, dict]]) -> dict[str, dict]:
    """Build {locale: {namespace: {key: value}}} from temp files.

    Each temp value has shape {"en": "...", "zh-TW": "..."}.
    We split that into separate locale dicts.
    """
    locales: dict[str, dict] = {}
    for _, data in temps:
        for namespace, keys in data.items():
            if not isinstance(keys, dict):
                continue
            for key, translations in keys.items():
                if not isinstance(translations, dict):
                    continue
                for locale, value in translations.items():
                    locales.setdefault(locale, {}).setdefault(namespace, {})[key] = (
                        value
                    )
    return locales


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="preview without writing"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove temp files after successful merge",
    )
    args = parser.parse_args()

    temps = load_temp_files()
    if not temps:
        print("No _new_keys_*.json files found in scripts/. Nothing to merge.")
        return 0

    print(f"Found {len(temps)} temp key file(s):")
    total_keys = 0
    for path, data in temps:
        count = sum(
            len(keys) if isinstance(keys, dict) else 0 for keys in data.values()
        )
        total_keys += count
        print(f"  {path.name}: {count} keys across {len(data)} namespace(s)")
    print(f"Total new keys to merge: {total_keys}")
    print()

    locale_map = build_locale_map(temps)
    print(f"Locales to update: {list(locale_map.keys())}")
    print()

    all_conflicts: list[str] = []
    written: list[Path] = []

    for locale, new_data in locale_map.items():
        target_path = I18N_DIR / f"{locale}.json"
        if not target_path.exists():
            print(f"SKIP {locale}: target file {target_path.name} does not exist")
            continue

        existing = json.loads(target_path.read_text(encoding="utf-8"))
        merged, conflicts = deep_merge(existing, new_data)

        if conflicts:
            print(f"CONFLICTS in {locale}.json:")
            for c in conflicts:
                print(f"  {c}")
            all_conflicts.extend(conflicts)
            # Continue anyway — keep existing value on conflict (deep_merge already did)
            print(f"  (kept existing values for {len(conflicts)} conflicts)")

        if not args.dry_run:
            # Backup
            backup = target_path.with_suffix(".json.bak")
            backup.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")

            target_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written.append(target_path)
            print(f"  Wrote {target_path.relative_to(ROOT)} (backup: {backup.name})")
        else:
            print(f"  [dry-run] would write {target_path.relative_to(ROOT)}")

    print()
    if args.dry_run:
        print(f"[dry-run] No files written. {total_keys} keys would be merged.")
    elif written:
        print(f"SUCCESS: merged into {len(written)} locale file(s).")
        if args.clean:
            for path, _ in temps:
                path.unlink()
                print(f"  Cleaned up {path.name}")
    if all_conflicts:
        print()
        print(
            f"WARNING: {len(all_conflicts)} conflict(s) detected (existing values kept)."
        )
        return 0  # conflicts are not fatal

    # Verify with check_i18n_keys.py
    print()
    print("Running check_i18n_keys.py to verify alignment...")
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_i18n_keys.py")],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
